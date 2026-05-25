from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.request import urlopen

from pyfhircheck.config import PackageConfig


@dataclass(frozen=True)
class ResolvedPackage:
    name: str
    version: str
    source: str
    path: str
    cached: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "path": self.path,
            "cached": self.cached,
        }


class PackageResolver:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)

    def resolve_all(self, packages: Iterable[PackageConfig]) -> list[ResolvedPackage]:
        return [self.resolve(package) for package in packages]

    def resolve(self, package: PackageConfig) -> ResolvedPackage:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{package.name}-{package.version}.tgz"
        if target.exists():
            return ResolvedPackage(package.name, package.version, package.source or _package_url(package), str(target), True)
        source = package.source or _package_url(package)
        _copy_source(source, target)
        return ResolvedPackage(package.name, package.version, source, str(target), False)


def iter_package_resources(paths: Iterable[str], remote_sources: Iterable[str] = ()) -> Iterable[dict[str, Any]]:
    for raw in paths:
        path = Path(raw)
        if path.suffix == ".tgz":
            yield from _iter_tgz(path)
        elif path.is_file():
            yield from _iter_file(path)
        elif path.is_dir():
            for file_path in sorted(path.rglob("*.json")):
                yield from _iter_file(file_path)
    for source in remote_sources:
        with urlopen(source, timeout=30) as response:
            payload = response.read()
        with tempfile.NamedTemporaryFile(suffix=".tgz") as tmp:
            tmp.write(payload)
            tmp.flush()
            yield from _iter_tgz(Path(tmp.name))


def iter_structure_definitions(paths: Iterable[str], remote_sources: Iterable[str] = ()) -> Iterable[dict[str, Any]]:
    for data in iter_package_resources(paths, remote_sources):
        if data.get("resourceType") == "StructureDefinition":
            yield data


def _iter_file(path: Path) -> Iterable[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    yield data


def _iter_tgz(path: Path) -> Iterable[dict[str, Any]]:
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            data = json.loads(extracted.read().decode("utf-8"))
            yield data


def _package_url(package: PackageConfig) -> str:
    registry = package.registry.rstrip("/")
    return f"{registry}/{package.name}/{package.version}"


def _copy_source(source: str, target: Path) -> None:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        with urlopen(source, timeout=60) as response:
            target.write_bytes(response.read())
        return
    if parsed.scheme == "file":
        shutil.copyfile(Path(parsed.path), target)
        return
    source_path = Path(source)
    if source_path.exists():
        shutil.copyfile(source_path, target)
        return
    with urlopen(source, timeout=60) as response:
        target.write_bytes(response.read())
