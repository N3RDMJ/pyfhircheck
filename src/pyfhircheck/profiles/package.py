from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.request import urlopen

from pyfhircheck.config import PackageConfig


@dataclass(frozen=True)
class PackageManifest:
    name: str
    version: str
    fhir_versions: tuple[str, ...] = ()
    dependencies: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedPackage:
    name: str
    version: str
    source: str
    path: str
    cached: bool
    manifest: PackageManifest | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "path": self.path,
            "cached": self.cached,
        }
        if self.manifest:
            data["dependencies"] = self.manifest.dependencies
        return data


def parse_package_manifest(tgz_path: Path) -> PackageManifest | None:
    try:
        with tarfile.open(tgz_path, "r:gz") as archive:
            for member in archive.getmembers():
                if member.name in ("package/package.json", "package.json") and member.isfile():
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    data = json.loads(extracted.read().decode("utf-8"))
                    fhir_versions = data.get("fhirVersions", data.get("fhir-version-list", []))
                    if isinstance(fhir_versions, str):
                        fhir_versions = [fhir_versions]
                    deps = data.get("dependencies", {})
                    return PackageManifest(
                        name=data.get("name", ""),
                        version=data.get("version", ""),
                        fhir_versions=tuple(fhir_versions) if isinstance(fhir_versions, list) else (),
                        dependencies=dict(deps) if isinstance(deps, dict) else {},
                    )
    except (tarfile.TarError, json.JSONDecodeError, OSError):
        pass
    return None


class PackageResolver:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)

    def resolve_all(self, packages: Iterable[PackageConfig]) -> list[ResolvedPackage]:
        return [self.resolve(package) for package in packages]

    def resolve_with_dependencies(self, packages: Iterable[PackageConfig]) -> list[ResolvedPackage]:
        resolved_map: dict[str, ResolvedPackage] = {}
        queue = list(packages)
        while queue:
            package = queue.pop(0)
            if package.name in resolved_map:
                continue
            resolved = self.resolve(package)
            resolved_map[package.name] = resolved
            if resolved.manifest:
                for dep_name, dep_version in resolved.manifest.dependencies.items():
                    if dep_name not in resolved_map and not dep_name.startswith("hl7.fhir.r4.examples"):
                        queue.append(PackageConfig(name=dep_name, version=dep_version, registry=package.registry))
        return _topological_sort(resolved_map)

    def resolve(self, package: PackageConfig) -> ResolvedPackage:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{package.name}-{package.version}.tgz"
        if target.exists():
            manifest = parse_package_manifest(target)
            return ResolvedPackage(package.name, package.version, package.source or _package_url(package), str(target), True, manifest)
        source = package.source or _package_url(package)
        _copy_source(source, target)
        manifest = parse_package_manifest(target)
        return ResolvedPackage(package.name, package.version, source, str(target), False, manifest)


def iter_package_resources(paths: Iterable[str], remote_sources: Iterable[str] = (), resource_types: set[str] | None = None) -> Iterable[dict[str, Any]]:
    for raw in paths:
        path = Path(raw)
        if path.suffix == ".tgz":
            yield from _iter_tgz(path, resource_types)
        elif path.is_file():
            yield from _iter_file(path, resource_types)
        elif path.is_dir():
            for file_path in sorted(path.rglob("*.json")):
                yield from _iter_file(file_path, resource_types)
    for source in remote_sources:
        payload = _read_url(source, timeout=30)
        if payload is None:
            continue
        with tempfile.NamedTemporaryFile(suffix=".tgz") as tmp:
            tmp.write(payload)
            tmp.flush()
            yield from _iter_tgz(Path(tmp.name), resource_types)


def iter_structure_definitions(paths: Iterable[str], remote_sources: Iterable[str] = ()) -> Iterable[dict[str, Any]]:
    yield from iter_package_resources(paths, remote_sources, {"StructureDefinition"})


def _iter_file(path: Path, resource_types: set[str] | None = None) -> Iterable[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return
    if _resource_type_matches(data, resource_types):
        yield data


def _iter_tgz(path: Path, resource_types: set[str] | None = None) -> Iterable[dict[str, Any]]:
    try:
        archive = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError):
        return
    with archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            try:
                data = json.loads(extracted.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            if _resource_type_matches(data, resource_types):
                yield data


def _topological_sort(resolved_map: dict[str, ResolvedPackage]) -> list[ResolvedPackage]:
    visited: set[str] = set()
    order: list[str] = []

    def visit(name: str) -> None:
        if name in visited or name not in resolved_map:
            return
        visited.add(name)
        pkg = resolved_map[name]
        if pkg.manifest:
            for dep_name in pkg.manifest.dependencies:
                visit(dep_name)
        order.append(name)

    for name in resolved_map:
        visit(name)
    return [resolved_map[name] for name in order]


def _package_url(package: PackageConfig) -> str:
    registry = package.registry.rstrip("/")
    return f"{registry}/{package.name}/{package.version}"


def _copy_source(source: str, target: Path) -> None:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        payload = _read_url(source, timeout=60)
        if payload is None:
            raise OSError(f"Could not download package source {source}")
        target.write_bytes(payload)
        return
    if parsed.scheme == "file":
        shutil.copyfile(Path(parsed.path), target)
        return
    source_path = Path(source)
    if source_path.exists():
        shutil.copyfile(source_path, target)
        return
    payload = _read_url(source, timeout=60)
    if payload is None:
        raise OSError(f"Could not download package source {source}")
    target.write_bytes(payload)


def _read_url(source: str, timeout: int, attempts: int = 3) -> bytes | None:
    for attempt in range(attempts):
        try:
            with urlopen(source, timeout=timeout) as response:
                return response.read()
        except Exception:  # noqa: BLE001 - package fetching should retry transient network failures.
            if attempt < attempts - 1:
                time.sleep(0.2 * (attempt + 1))
    return None


def _resource_type_matches(data: Any, resource_types: set[str] | None) -> bool:
    if not isinstance(data, dict):
        return False
    return resource_types is None or data.get("resourceType") in resource_types
