from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


FHIR_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+/[A-Za-z0-9\-.]{1,64}(/_history/[A-Za-z0-9\-.]{1,64})?$")
ID_RE = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
DATETIME_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?)?)?$")
INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T.+(Z|[+-]\d{2}:\d{2})$")


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(canonical_json(part).encode("utf-8"))
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_file(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{exc.msg} at line {exc.lineno}, column {exc.colno}"
    except OSError as exc:
        return None, str(exc)


def iter_json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.json") if p.is_file())


def resource_key(resource: dict[str, Any]) -> str | None:
    resource_type = resource.get("resourceType")
    resource_id = resource.get("id")
    if isinstance(resource_type, str) and isinstance(resource_id, str):
        return f"{resource_type}/{resource_id}"
    return None


def get_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def has_path(data: Any, path: str) -> bool:
    sentinel = object()
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return False
        current = current.get(part, sentinel)
        if current is sentinel:
            return False
    return True


def values_at_path(data: Any, path: str) -> list[Any]:
    current_values = [data]
    for part in path.split("."):
        next_values: list[Any] = []
        if part.endswith("[x]"):
            prefix = part[:-3]
            for value in current_values:
                _collect_choice_values(value, prefix, next_values)
        else:
            for value in current_values:
                _collect_field_values(value, part, next_values)
        current_values = next_values
        if not current_values:
            return []
    return current_values


def _collect_field_values(value: Any, part: str, out: list[Any]) -> None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and part in item:
                child = item[part]
                if isinstance(child, list):
                    out.extend(child)
                else:
                    out.append(child)
    elif isinstance(value, dict) and part in value:
        child = value[part]
        if isinstance(child, list):
            out.extend(child)
        else:
            out.append(child)


def _collect_choice_values(value: Any, prefix: str, out: list[Any]) -> None:
    targets = [value] if isinstance(value, dict) else value if isinstance(value, list) else []
    for target in targets:
        if not isinstance(target, dict):
            continue
        for key, child in target.items():
            if key.startswith(prefix) and key != prefix and key[len(prefix):][0:1].isupper():
                if isinstance(child, list):
                    out.extend(child)
                else:
                    out.append(child)
