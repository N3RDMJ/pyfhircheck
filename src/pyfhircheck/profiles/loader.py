from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyfhircheck.profiles.package import iter_structure_definitions
from pyfhircheck.profiles.snapshot import SnapshotResolver


@dataclass(frozen=True)
class ElementConstraint:
    path: str
    min: int = 0
    max: str = "1"
    fixed: Any | None = None
    pattern: Any | None = None
    binding: tuple[str, str] | None = None
    invariants: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class SliceConstraint:
    path: str
    name: str
    min: int = 0
    max: str = "*"
    discriminators: tuple[tuple[str, str], ...] = ()
    elements: dict[str, ElementConstraint] | None = None


@dataclass(frozen=True)
class ProfileConstraint:
    url: str
    resource_type: str
    required: tuple[str, ...] = ()
    cardinality: dict[str, tuple[int, str]] | None = None
    fixed: dict[str, Any] | None = None
    patterns: dict[str, Any] | None = None
    bindings: dict[str, tuple[str, str]] | None = None
    invariants: tuple[tuple[str, str, str], ...] = ()
    elements: dict[str, ElementConstraint] | None = None
    slices: dict[str, SliceConstraint] | None = None


@dataclass(frozen=True)
class ExtensionConstraint:
    url: str
    is_modifier: bool = False
    value_types: tuple[str, ...] = ()
    min_value: int = 0
    max_value: str = "1"
    nested_extensions: dict[str, ElementConstraint] | None = None


class ProfileRegistry:
    def __init__(self) -> None:
        self._profiles: dict[str, ProfileConstraint] = {}
        self._extensions: dict[str, ExtensionConstraint] = {}
        self.merged_snapshots = 0
        self._install_builtin_profiles()

    def _install_builtin_profiles(self) -> None:
        self.add(
            ProfileConstraint(
                url="http://example.org/fhir/StructureDefinition/patient-with-identifier",
                resource_type="Patient",
                required=("identifier",),
            )
        )

    def add(self, profile: ProfileConstraint) -> None:
        self._profiles[profile.url] = profile

    def load_paths(self, paths: list[str]) -> None:
        self._load_many(list(iter_structure_definitions(paths)))

    def load_remote_sources(self, sources: list[str]) -> None:
        self._load_many(list(iter_structure_definitions([], sources)))

    def _load_many(self, structure_definitions: list[dict[str, Any]]) -> None:
        resolver = SnapshotResolver(structure_definitions)
        for data in structure_definitions:
            self._load_structure_definition(data, resolver.elements_for(data))
        self.merged_snapshots += resolver.merged_count

    def _load_structure_definition(self, data: dict[str, Any], elements: list[dict[str, Any]] | None = None) -> None:
        url = data.get("url")
        resource_type = data.get("type")
        if not isinstance(url, str) or not isinstance(resource_type, str):
            return
        if resource_type == "Extension":
            self._load_extension_definition(data, elements or [])
        required: list[str] = []
        cardinality: dict[str, tuple[int, str]] = {}
        fixed: dict[str, Any] = {}
        patterns: dict[str, Any] = {}
        bindings: dict[str, tuple[str, str]] = {}
        invariants: list[tuple[str, str, str]] = []
        element_constraints: dict[str, ElementConstraint] = {}
        slice_constraints: dict[str, dict[str, Any]] = {}
        slicing_discriminators: dict[str, tuple[tuple[str, str], ...]] = {}
        elements = elements if elements is not None else data.get("snapshot", {}).get("element", []) or data.get("differential", {}).get("element", [])
        for element in elements:
            path = element.get("path")
            if not isinstance(path, str):
                continue
            if path == resource_type:
                for constraint in element.get("constraint", []):
                    if isinstance(constraint, dict) and isinstance(constraint.get("expression"), str):
                        invariants.append((constraint.get("key", "constraint"), constraint.get("severity", "error"), constraint["expression"]))
                continue
            if "." not in path:
                continue
            field = path.split(".", 1)[1]
            normalized_field, slice_name, slice_child = _parse_slice_path(field, element)
            slicing = element.get("slicing")
            if isinstance(slicing, dict):
                discriminators = _parse_discriminators(slicing)
                if discriminators:
                    slicing_discriminators[normalized_field] = discriminators
            min_value = int(element.get("min", 0))
            max_value = str(element.get("max", "1"))
            binding = element.get("binding")
            if isinstance(binding, dict) and isinstance(binding.get("valueSet"), str):
                binding_pair = (binding.get("strength", "example"), binding["valueSet"])
            else:
                binding_pair = None
            fixed_value = None
            pattern_value = None
            element_invariants: list[tuple[str, str, str]] = []
            for constraint in element.get("constraint", []):
                if isinstance(constraint, dict) and isinstance(constraint.get("expression"), str):
                    element_invariants.append((constraint.get("key", "constraint"), constraint.get("severity", "error"), constraint["expression"]))
            for key, value in element.items():
                if key.startswith("fixed"):
                    fixed_value = value
                if key.startswith("pattern"):
                    pattern_value = value
            constraint_path = f"{normalized_field}.{slice_child}" if slice_child else normalized_field
            element_constraint = ElementConstraint(
                path=constraint_path,
                min=min_value,
                max=max_value,
                fixed=fixed_value,
                pattern=pattern_value,
                binding=binding_pair,
                invariants=tuple(element_invariants),
            )
            if slice_name is not None:
                key = f"{normalized_field}:{slice_name}"
                slice_data = slice_constraints.setdefault(
                    key,
                    {
                        "path": normalized_field,
                        "name": slice_name,
                        "min": 0,
                        "max": "*",
                        "elements": {},
                    },
                )
                if slice_child is None:
                    slice_data["min"] = min_value
                    slice_data["max"] = max_value
                else:
                    slice_data["elements"][slice_child] = element_constraint
                continue
            element_constraints[constraint_path] = element_constraint
            if "." in normalized_field:
                continue
            cardinality[normalized_field] = (min_value, max_value)
            if min_value > 0:
                required.append(normalized_field)
            if binding_pair is not None:
                bindings[normalized_field] = binding_pair
            invariants.extend(element_invariants)
            if fixed_value is not None:
                fixed[normalized_field] = fixed_value
            if pattern_value is not None:
                patterns[normalized_field] = pattern_value
        slices = {
            key: SliceConstraint(
                path=value["path"],
                name=value["name"],
                min=value["min"],
                max=value["max"],
                discriminators=slicing_discriminators.get(value["path"], ()),
                elements=value["elements"],
            )
            for key, value in slice_constraints.items()
        }
        self.add(
            ProfileConstraint(
                url=url,
                resource_type=resource_type,
                required=tuple(required),
                cardinality=cardinality,
                fixed=fixed,
                patterns=patterns,
                bindings=bindings,
                invariants=tuple(invariants),
                elements=element_constraints,
                slices=slices,
            )
        )

    def get(self, url: str) -> ProfileConstraint | None:
        return self._profiles.get(url)

    def get_extension(self, url: str) -> ExtensionConstraint | None:
        return self._extensions.get(url)

    def _load_extension_definition(self, data: dict[str, Any], elements: list[dict[str, Any]]) -> None:
        url = data.get("url")
        if not isinstance(url, str):
            return
        value_types: list[str] = []
        min_value = 0
        max_value = "1"
        nested: dict[str, ElementConstraint] = {}
        is_modifier = bool(data.get("isModifier", False))
        for element in elements:
            path = element.get("path")
            if not isinstance(path, str):
                continue
            if path == "Extension":
                is_modifier = bool(element.get("isModifier", is_modifier))
            if path == "Extension.url":
                fixed = next((value for key, value in element.items() if key.startswith("fixed")), None)
                if fixed and fixed != url:
                    continue
            if path == "Extension.value[x]":
                min_value = int(element.get("min", 0))
                max_value = str(element.get("max", "1"))
                value_types.extend(
                    str(type_entry.get("code"))
                    for type_entry in element.get("type", [])
                    if isinstance(type_entry, dict) and type_entry.get("code")
                )
            if path.startswith("Extension.extension:"):
                field, slice_name, slice_child = _parse_slice_path(path.split(".", 1)[1], element)
                if slice_name and slice_child == "url":
                    fixed = next((value for key, value in element.items() if key.startswith("fixed")), None)
                    if fixed:
                        nested[slice_name] = ElementConstraint(path=field, min=int(element.get("min", 0)), max=str(element.get("max", "1")), fixed=fixed)
        self._extensions[url] = ExtensionConstraint(
            url=url,
            is_modifier=is_modifier,
            value_types=tuple(dict.fromkeys(value_types)),
            min_value=min_value,
            max_value=max_value,
            nested_extensions=nested,
        )


def _parse_slice_path(field: str, element: dict[str, Any]) -> tuple[str, str | None, str | None]:
    slice_name = element.get("sliceName")
    parts = field.split(".")
    normalized: list[str] = []
    child_parts: list[str] = []
    found_slice: str | None = slice_name if isinstance(slice_name, str) else None
    for index, part in enumerate(parts):
        if ":" in part:
            base, parsed_slice = part.split(":", 1)
            normalized.append(base)
            found_slice = found_slice or parsed_slice
            child_parts = parts[index + 1 :]
            break
        normalized.append(part)
    if found_slice is not None and not child_parts and len(parts) > 1 and ":" not in field:
        normalized = [parts[0]]
        child_parts = parts[1:]
    return ".".join(normalized), found_slice, ".".join(child_parts) if child_parts else None


def _parse_discriminators(slicing: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    discriminators: list[tuple[str, str]] = []
    for discriminator in slicing.get("discriminator", []):
        if not isinstance(discriminator, dict):
            continue
        kind = discriminator.get("type")
        path = discriminator.get("path")
        if isinstance(kind, str) and isinstance(path, str):
            discriminators.append((kind, path))
    return tuple(discriminators)
