from __future__ import annotations

from typing import Any, Iterable


class SnapshotResolver:
    def __init__(self, structure_definitions: Iterable[dict[str, Any]]):
        self._by_url: dict[str, dict[str, Any]] = {}
        self._by_type: dict[str, dict[str, Any]] = {}
        self._resolved_cache: dict[str, list[dict[str, Any]]] = {}
        self.merged_count = 0
        for sd in structure_definitions:
            url = sd.get("url")
            type_name = sd.get("type")
            if isinstance(url, str):
                self._by_url[url] = sd
            if isinstance(type_name, str) and sd.get("snapshot", {}).get("element"):
                self._by_type.setdefault(type_name, sd)

    def elements_for(self, sd: dict[str, Any]) -> list[dict[str, Any]]:
        url = sd.get("url")
        if isinstance(url, str) and url in self._resolved_cache:
            return self._resolved_cache[url]
        snapshot = sd.get("snapshot", {}).get("element")
        if isinstance(snapshot, list) and snapshot:
            return snapshot
        differential = sd.get("differential", {}).get("element")
        if not isinstance(differential, list) or not differential:
            return []
        base = self._resolve_base_elements(sd, set())
        if not base:
            return differential
        self.merged_count += 1
        merged = _merge_elements(base, differential)
        if isinstance(url, str):
            self._resolved_cache[url] = merged
        return merged

    def _resolve_base_elements(self, sd: dict[str, Any], visited: set[str]) -> list[dict[str, Any]]:
        url = sd.get("url")
        if isinstance(url, str) and url in visited:
            return []
        if isinstance(url, str):
            visited.add(url)

        base_definition = sd.get("baseDefinition")
        if not isinstance(base_definition, str):
            type_name = sd.get("type")
            if isinstance(type_name, str):
                type_sd = self._by_type.get(type_name)
                if type_sd is not None and type_sd is not sd:
                    snapshot = type_sd.get("snapshot", {}).get("element")
                    if isinstance(snapshot, list):
                        return snapshot
            return []

        base_sd = self._by_url.get(base_definition)
        if base_sd is None:
            type_name = sd.get("type")
            if isinstance(type_name, str):
                type_sd = self._by_type.get(type_name)
                if type_sd is not None and type_sd is not sd:
                    snapshot = type_sd.get("snapshot", {}).get("element")
                    if isinstance(snapshot, list):
                        return snapshot
            return []

        if base_sd is sd:
            return []

        base_snapshot = base_sd.get("snapshot", {}).get("element")
        if isinstance(base_snapshot, list) and base_snapshot:
            return base_snapshot

        base_differential = base_sd.get("differential", {}).get("element")
        if not isinstance(base_differential, list) or not base_differential:
            return []

        grandparent_elements = self._resolve_base_elements(base_sd, visited)
        if not grandparent_elements:
            return base_differential
        self.merged_count += 1
        merged = _merge_elements(grandparent_elements, base_differential)
        base_url = base_sd.get("url")
        if isinstance(base_url, str):
            self._resolved_cache[base_url] = merged
        return merged


def _merge_elements(base: list[dict[str, Any]], differential: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_by_path: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for element in base:
        path = element.get("path")
        if not isinstance(path, str):
            continue
        key = _element_key(element)
        merged_by_path[key] = dict(element)
        order.append(key)

    insert_after: str | None = None
    current_slice_scope: dict[str, str] = {}
    for diff in differential:
        path = diff.get("path")
        if not isinstance(path, str):
            continue
        slice_name = diff.get("sliceName")
        if isinstance(slice_name, str):
            current_slice_scope[path] = slice_name
        elif "." in path:
            parent = path.rsplit(".", 1)[0]
            if parent not in current_slice_scope:
                for prefix in sorted(current_slice_scope, key=len, reverse=True):
                    if path.startswith(prefix + "."):
                        break
                else:
                    prefix = None
                if prefix is not None:
                    parent = prefix
            if parent in current_slice_scope:
                pass
        else:
            current_slice_scope.pop(path, None)
        key = _element_key_with_scope(diff, current_slice_scope)
        if key in merged_by_path:
            merged = _merge_single_element(merged_by_path[key], diff)
            merged_by_path[key] = merged
            insert_after = key
        else:
            merged_by_path[key] = dict(diff)
            if insert_after is not None and insert_after in order:
                idx = order.index(insert_after) + 1
                order.insert(idx, key)
                insert_after = key
            else:
                order.append(key)
                insert_after = key

    return [merged_by_path[key] for key in order if key in merged_by_path]


def _element_key(element: dict[str, Any]) -> str:
    path = str(element.get("path", ""))
    slice_name = element.get("sliceName")
    if isinstance(slice_name, str):
        return f"{path}:{slice_name}"
    return path


def _element_key_with_scope(element: dict[str, Any], scope: dict[str, str]) -> str:
    path = str(element.get("path", ""))
    slice_name = element.get("sliceName")
    if isinstance(slice_name, str):
        return f"{path}:{slice_name}"
    if "." in path:
        for prefix in sorted(scope, key=len, reverse=True):
            if path.startswith(prefix + "."):
                return f"{prefix}:{scope[prefix]}.{path[len(prefix) + 1:]}"
    return path


def _merge_single_element(base: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in diff.items():
        if key == "min":
            base_min = base.get("min", 0)
            merged["min"] = max(_safe_int(value), _safe_int(base_min))
        elif key == "max":
            merged["max"] = _constrain_max(base.get("max", "*"), str(value))
        elif key == "binding":
            merged["binding"] = _merge_binding(base.get("binding"), value)
        elif key == "constraint":
            base_constraints = base.get("constraint", [])
            if isinstance(base_constraints, list) and isinstance(value, list):
                existing_keys = {c.get("key") for c in base_constraints if isinstance(c, dict)}
                new_constraints = [c for c in value if isinstance(c, dict) and c.get("key") not in existing_keys]
                merged["constraint"] = base_constraints + new_constraints
            else:
                merged[key] = value
        elif key == "slicing":
            merged["slicing"] = value
        else:
            merged[key] = value
    return merged


def _constrain_max(base_max: str, diff_max: str) -> str:
    if base_max == "*":
        return diff_max
    if diff_max == "*":
        return base_max
    try:
        return str(min(int(base_max), int(diff_max)))
    except ValueError:
        return diff_max


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


BINDING_STRENGTH_ORDER = {"required": 3, "extensible": 2, "preferred": 1, "example": 0}


def _merge_binding(base_binding: Any, diff_binding: Any) -> Any:
    if not isinstance(base_binding, dict) or not isinstance(diff_binding, dict):
        return diff_binding
    merged = dict(base_binding)
    merged.update(diff_binding)
    base_strength = BINDING_STRENGTH_ORDER.get(str(base_binding.get("strength", "")), -1)
    diff_strength = BINDING_STRENGTH_ORDER.get(str(diff_binding.get("strength", "")), -1)
    if diff_strength >= base_strength:
        merged["strength"] = diff_binding.get("strength", base_binding.get("strength"))
    else:
        merged["strength"] = base_binding.get("strength")
    return merged
