from __future__ import annotations

from typing import Any, Iterable


class SnapshotResolver:
    def __init__(self, structure_definitions: Iterable[dict[str, Any]]):
        self._by_url: dict[str, dict[str, Any]] = {}
        self._by_type: dict[str, dict[str, Any]] = {}
        self.merged_count = 0
        for sd in structure_definitions:
            url = sd.get("url")
            type_name = sd.get("type")
            if isinstance(url, str):
                self._by_url[url] = sd
            if isinstance(type_name, str) and sd.get("snapshot", {}).get("element"):
                self._by_type[type_name] = sd

    def elements_for(self, sd: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = sd.get("snapshot", {}).get("element")
        if isinstance(snapshot, list) and snapshot:
            return snapshot
        differential = sd.get("differential", {}).get("element")
        if not isinstance(differential, list) or not differential:
            return []
        base = self._base_elements(sd)
        if not base:
            return differential
        self.merged_count += 1
        return _merge_elements(base, differential)

    def _base_elements(self, sd: dict[str, Any]) -> list[dict[str, Any]]:
        base_definition = sd.get("baseDefinition")
        base_sd = self._by_url.get(base_definition) if isinstance(base_definition, str) else None
        if base_sd is None and isinstance(sd.get("type"), str):
            base_sd = self._by_type.get(sd["type"])
        if base_sd is None or base_sd is sd:
            return []
        snapshot = base_sd.get("snapshot", {}).get("element")
        return snapshot if isinstance(snapshot, list) else []


def _merge_elements(base: list[dict[str, Any]], differential: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_by_path = {
        element.get("path"): dict(element)
        for element in base
        if isinstance(element.get("path"), str)
    }
    order = [element.get("path") for element in base if isinstance(element.get("path"), str)]
    for diff in differential:
        path = diff.get("path")
        if not isinstance(path, str):
            continue
        if path not in merged_by_path:
            order.append(path)
            merged_by_path[path] = dict(diff)
        else:
            merged = dict(merged_by_path[path])
            merged.update(diff)
            merged_by_path[path] = merged
    return [merged_by_path[path] for path in order if path in merged_by_path]
