from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyfhircheck.config import TerminologyConfig
from pyfhircheck.core.definitions import VALUE_SETS
from pyfhircheck.profiles.package import iter_package_resources


@dataclass
class Concept:
    code: str
    display: str | None = None
    properties: dict[str, set[str]] = field(default_factory=dict)
    designations: set[str] = field(default_factory=set)


class TerminologyResolver:
    def __init__(self, config: TerminologyConfig, package_paths: list[str] | None = None, remote_sources: list[str] | None = None):
        self.config = config
        self.package_code_systems: dict[str, dict[str, Concept]] = {}
        self.package_value_set_definitions: dict[str, dict[str, Any]] = {}
        self._value_set_cache: dict[str, set[str]] = {}
        self.loaded_code_systems = 0
        self.loaded_value_sets = 0
        self._load_packages(package_paths or [], remote_sources or [])

    def validate_coding(self, system: str, code: str) -> bool | None:
        if self.config.mode == "off":
            return None
        if system in self.config.ignored_code_systems:
            return None
        concepts = self.package_code_systems.get(system) or self.package_code_systems.get(system.rsplit("/", 1)[-1])
        if concepts is None or len(concepts) == 0:
            return None
        return code in concepts

    def validate_display(self, system: str, code: str, display: str) -> str | None:
        if self.config.mode == "off":
            return None
        concepts = self.package_code_systems.get(system) or self.package_code_systems.get(system.rsplit("/", 1)[-1])
        if concepts is None:
            return None
        concept = concepts.get(code)
        if concept is None:
            return None
        valid_displays: list[str] = []
        if concept.display:
            valid_displays.append(concept.display)
        valid_displays.extend(concept.designations)
        if not valid_displays:
            return None
        if display in valid_displays:
            return None
        return f"Wrong Display Name '{display}' for {system}#{code}. Valid display is one of {len(valid_displays)} choices: {', '.join(repr(d) for d in valid_displays[:3])}"

    def contains(self, value_set: str, code: str) -> bool | None:
        if self.config.mode == "off":
            return None
        if value_set in self.config.ignored_value_sets or value_set.rsplit("/", 1)[-1] in self.config.ignored_value_sets:
            return None
        if value_set in self.config.ignored_code_systems or value_set.rsplit("/", 1)[-1] in self.config.ignored_code_systems:
            return None
        configured = self.config.code_systems.get(value_set)
        allowed = (
            set(configured)
            if configured is not None
            else VALUE_SETS.get(value_set)
            or self._expanded_value_set(value_set)
            or self._expanded_value_set(value_set.rsplit("/", 1)[-1])
            or set(self.package_code_systems.get(value_set, {}))
            or set(self.package_code_systems.get(value_set.rsplit("/", 1)[-1], {}))
        )
        if allowed is None:
            return None
        return code in allowed

    def evidence(self) -> dict[str, Any]:
        return {
            **self.config.to_dict(),
            "loadedCodeSystems": self.loaded_code_systems,
            "loadedValueSets": self.loaded_value_sets,
            "expandedPackageValueSets": len(self._value_set_cache),
        }

    def load_value_sets_from(self, paths: list[str]) -> None:
        for resource in iter_package_resources(paths, [], {"ValueSet", "CodeSystem"}):
            rt = resource.get("resourceType")
            if rt == "ValueSet":
                self._load_value_set(resource)
            elif rt == "CodeSystem" and resource.get("concept"):
                self._load_code_system(resource)

    def _load_packages(self, paths: list[str], remote_sources: list[str]) -> None:
        for resource in iter_package_resources(paths, remote_sources, {"CodeSystem", "ValueSet"}):
            resource_type = resource.get("resourceType")
            if resource_type == "CodeSystem":
                self._load_code_system(resource)
            elif resource_type == "ValueSet":
                self._load_value_set(resource)

    def _load_code_system(self, resource: dict) -> None:
        concepts = _concepts(resource.get("concept", []))
        for key in _resource_keys(resource):
            self.package_code_systems[key] = concepts
        self.loaded_code_systems += 1

    def _load_value_set(self, resource: dict) -> None:
        for key in _resource_keys(resource):
            self.package_value_set_definitions[key] = resource
        self.loaded_value_sets += 1

    def _expanded_value_set(self, key: str) -> set[str] | None:
        if key in self._value_set_cache:
            return self._value_set_cache[key]
        resource = self.package_value_set_definitions.get(key)
        if resource is None:
            return None
        codes: set[str] = set()
        compose = resource.get("compose", {})
        if isinstance(compose, dict):
            for include in compose.get("include", []):
                codes.update(self._codes_for_include(include))
            for exclude in compose.get("exclude", []):
                codes.difference_update(self._codes_for_include(exclude))
        expansion = resource.get("expansion", {})
        if isinstance(expansion, dict):
            for contains in expansion.get("contains", []):
                codes.update(_expansion_codes(contains))
        for resource_key in _resource_keys(resource):
            self._value_set_cache[resource_key] = codes
        return codes

    def _codes_for_include(self, include: Any) -> set[str]:
        if not isinstance(include, dict):
            return set()
        explicit = {
            concept["code"]
            for concept in include.get("concept", [])
            if isinstance(concept, dict) and isinstance(concept.get("code"), str)
        }
        system = include.get("system")
        if not isinstance(system, str):
            return explicit
        concepts = self.package_code_systems.get(system) or self.package_code_systems.get(system.rsplit("/", 1)[-1]) or {}
        if explicit:
            return explicit
        filtered = set(concepts)
        for filter_def in include.get("filter", []):
            filtered = {
                code
                for code, concept in concepts.items()
                if code in filtered and _matches_filter(concept, filter_def)
            }
        return filtered


def _resource_keys(resource: dict) -> list[str]:
    keys: list[str] = []
    for field in ("url", "id", "name"):
        value = resource.get(field)
        if isinstance(value, str):
            keys.append(value)
            keys.append(value.rsplit("/", 1)[-1])
    return list(dict.fromkeys(keys))


def _concepts(concepts: list) -> dict[str, Concept]:
    loaded: dict[str, Concept] = {}
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        code = concept.get("code")
        if isinstance(code, str):
            loaded[code] = Concept(
                code=code,
                display=concept.get("display") if isinstance(concept.get("display"), str) else None,
                properties=_properties(concept.get("property", [])),
                designations=_designations(concept.get("designation", [])),
            )
        loaded.update(_concepts(concept.get("concept", [])))
    return loaded


def _properties(properties: list) -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    for prop in properties:
        if not isinstance(prop, dict) or not isinstance(prop.get("code"), str):
            continue
        values = parsed.setdefault(prop["code"], set())
        for key, value in prop.items():
            if key.startswith("value") and isinstance(value, (str, bool, int, float)):
                values.add(str(value).lower() if isinstance(value, bool) else str(value))
    return parsed


def _designations(designations: list) -> set[str]:
    values: set[str] = set()
    for designation in designations:
        if isinstance(designation, dict) and isinstance(designation.get("value"), str):
            values.add(designation["value"])
    return values


def _expansion_codes(contains: Any) -> set[str]:
    codes: set[str] = set()
    if not isinstance(contains, dict):
        return codes
    if isinstance(contains.get("code"), str):
        codes.add(contains["code"])
    for child in contains.get("contains", []):
        codes.update(_expansion_codes(child))
    return codes


def _matches_filter(concept: Concept, filter_def: Any) -> bool:
    if not isinstance(filter_def, dict):
        return True
    prop = filter_def.get("property")
    op = filter_def.get("op")
    value = filter_def.get("value")
    if not isinstance(prop, str) or not isinstance(op, str) or value is None:
        return True
    expected = str(value)
    values = _filter_values(concept, prop)
    if op == "=":
        return expected in values
    if op == "is-a":
        return expected == concept.code or expected in values
    if op == "descendent-of":
        return expected in values and expected != concept.code
    if op == "in":
        allowed = {part.strip() for part in expected.split(",")}
        return bool(values & allowed)
    if op == "not-in":
        blocked = {part.strip() for part in expected.split(",")}
        return not bool(values & blocked)
    if op == "regex":
        import re

        return any(re.search(expected, candidate) for candidate in values)
    if op == "exists":
        want_exists = expected.lower() == "true"
        return bool(values) is want_exists
    return True


def _filter_values(concept: Concept, prop: str) -> set[str]:
    if prop == "code":
        return {concept.code}
    if prop == "display":
        return {concept.display} if concept.display else set()
    if prop == "designation":
        return set(concept.designations)
    return set(concept.properties.get(prop, set()))
