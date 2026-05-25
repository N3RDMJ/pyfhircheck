from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyfhircheck.core.definitions import COMMON_ELEMENTS, COMPLEX_TYPE_FIELDS, ElementDef, ResourceDef
from pyfhircheck.profiles.package import iter_structure_definitions
from pyfhircheck.profiles.snapshot import SnapshotResolver


PRIMITIVE_TYPES = {
    "base64Binary",
    "boolean",
    "canonical",
    "code",
    "date",
    "dateTime",
    "decimal",
    "id",
    "instant",
    "integer",
    "markdown",
    "oid",
    "positiveInt",
    "string",
    "time",
    "unsignedInt",
    "uri",
    "url",
    "uuid",
    "xhtml",
}

PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "boolean": (bool,),
    "integer": (int,),
    "positiveInt": (int,),
    "unsignedInt": (int,),
    "decimal": (int, float),
    "string": (str,),
    "markdown": (str,),
    "code": (str,),
    "id": (str,),
    "uri": (str,),
    "url": (str,),
    "canonical": (str,),
    "oid": (str,),
    "uuid": (str,),
    "date": (str,),
    "dateTime": (str,),
    "instant": (str,),
    "time": (str,),
    "base64Binary": (str,),
    "xhtml": (str,),
}


@dataclass
class SpecificationDefinitions:
    resources: dict[str, ResourceDef] = field(default_factory=dict)
    complex_types: dict[str, dict[str, tuple[type, ...]]] = field(default_factory=dict)
    loaded_structure_definitions: int = 0
    merged_snapshots: int = 0

    @classmethod
    def load(cls, paths: list[str], remote_sources: list[str]) -> "SpecificationDefinitions":
        definitions = cls()
        structure_definitions = list(iter_structure_definitions(paths, remote_sources))
        resolver = SnapshotResolver(structure_definitions)
        for structure_definition in structure_definitions:
            definitions.loaded_structure_definitions += 1
            definitions._ingest(structure_definition, resolver.elements_for(structure_definition))
        definitions.merged_snapshots = resolver.merged_count
        return definitions

    def _ingest(self, sd: dict[str, Any], elements: list[dict[str, Any]]) -> None:
        kind = sd.get("kind")
        type_name = sd.get("type")
        if not isinstance(type_name, str):
            return
        if not isinstance(elements, list):
            return
        if kind == "resource":
            resource_def = self._resource_def(type_name, elements)
            if resource_def.elements:
                self.resources[type_name] = resource_def
        elif kind == "complex-type":
            fields = self._complex_type_fields(type_name, elements)
            if fields:
                self.complex_types[type_name] = fields

    def _resource_def(self, resource_type: str, elements: list[dict[str, Any]]) -> ResourceDef:
        resource_elements = dict(COMMON_ELEMENTS)
        required: list[str] = []
        choice_groups: dict[str, list[str]] = {}
        for element in elements:
            field = _direct_child(resource_type, element.get("path"))
            if field is None or field in {"id", "meta", "implicitRules", "language", "text", "contained", "extension", "modifierExtension"}:
                continue
            element_def = _element_def(element)
            if element_def is None:
                continue
            resource_elements[field] = element_def
            if element_def.min > 0:
                required.append(field)
            if "[x]" in field:
                prefix = field.replace("[x]", "")
                choices = _choice_names(prefix, element)
                choice_groups[prefix] = choices
                for choice, type_code in zip(choices, element_def.types, strict=False):
                    resource_elements[choice] = ElementDef(
                        types=(type_code,),
                        min=0,
                        max=element_def.max,
                        required_binding=element_def.required_binding,
                        extensible_binding=element_def.extensible_binding,
                        target_types=element_def.target_types,
                        modifier=element_def.modifier,
                    )
        for choices in choice_groups.values():
            for choice in choices:
                current = resource_elements.get(choice)
                if current is not None:
                    resource_elements[choice] = ElementDef(
                        types=current.types,
                        min=current.min,
                        max=current.max,
                        required_binding=current.required_binding,
                        extensible_binding=current.extensible_binding,
                        target_types=current.target_types,
                        modifier=current.modifier,
                        choices=tuple(choices),
                    )
        return ResourceDef(required=tuple(sorted(set(required))), elements=resource_elements)

    def _complex_type_fields(self, type_name: str, elements: list[dict[str, Any]]) -> dict[str, tuple[type, ...]]:
        fields: dict[str, tuple[type, ...]] = {}
        for element in elements:
            field = _direct_child(type_name, element.get("path"))
            if field is None:
                continue
            py_types = _python_types(element)
            if py_types:
                fields[field] = py_types
        return fields


def merged_complex_types(loaded: dict[str, dict[str, tuple[type, ...]]]) -> dict[str, dict[str, tuple[type, ...]]]:
    merged = {name: dict(fields) for name, fields in COMPLEX_TYPE_FIELDS.items()}
    for name, fields in loaded.items():
        merged.setdefault(name, {}).update(fields)
    return merged


def _direct_child(root: str, path: Any) -> str | None:
    if not isinstance(path, str) or not path.startswith(f"{root}."):
        return None
    remainder = path[len(root) + 1 :]
    if "." in remainder:
        return None
    return remainder


def _element_def(element: dict[str, Any]) -> ElementDef | None:
    type_codes = [type_entry.get("code") for type_entry in element.get("type", []) if isinstance(type_entry, dict)]
    if not type_codes:
        return None
    binding = element.get("binding") if isinstance(element.get("binding"), dict) else {}
    strength = binding.get("strength")
    value_set = binding.get("valueSet")
    binding_name = value_set.rsplit("/", 1)[-1] if isinstance(value_set, str) else None
    target_profiles: list[str] = []
    for type_entry in element.get("type", []):
        if isinstance(type_entry, dict) and type_entry.get("code") == "Reference":
            for profile in type_entry.get("targetProfile", []):
                if isinstance(profile, str):
                    target_profiles.append(profile.rsplit("/", 1)[-1])
    return ElementDef(
        types=tuple(str(code) for code in type_codes),
        min=int(element.get("min", 0)),
        max=str(element.get("max", "1")),
        required_binding=binding_name if strength == "required" else None,
        extensible_binding=binding_name if strength == "extensible" else None,
        target_types=tuple(sorted(set(target_profiles))),
        modifier=bool(element.get("isModifier", False)),
    )


def _choice_names(prefix: str, element: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for type_entry in element.get("type", []):
        if not isinstance(type_entry, dict) or not isinstance(type_entry.get("code"), str):
            continue
        code = type_entry["code"]
        suffix = "Canonical" if code == "canonical" else code[:1].upper() + code[1:]
        names.append(f"{prefix}{suffix}")
    return names


def _python_types(element: dict[str, Any]) -> tuple[type, ...]:
    type_codes = [type_entry.get("code") for type_entry in element.get("type", []) if isinstance(type_entry, dict)]
    types: list[type] = []
    for code in type_codes:
        mapped = PYTHON_TYPES.get(str(code))
        if mapped:
            types.extend(mapped)
        else:
            types.append(dict)
    if element.get("max") == "*":
        return (list,)
    return tuple(dict.fromkeys(types))
