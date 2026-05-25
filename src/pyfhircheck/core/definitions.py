from __future__ import annotations

import importlib
import pkgutil
import types
import typing
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol, cast


@dataclass(frozen=True)
class ElementDef:
    types: tuple[str, ...]
    min: int = 0
    max: str = "1"
    required_binding: str | None = None
    extensible_binding: str | None = None
    target_types: tuple[str, ...] = ()
    modifier: bool = False
    choices: tuple[str, ...] = ()
    children: dict[str, "ElementDef"] | None = None


@dataclass(frozen=True)
class ResourceDef:
    required: tuple[str, ...] = ()
    elements: dict[str, ElementDef] = field(default_factory=dict)


_PRIMITIVE_MAP = {
    "Boolean": "boolean",
    "String": "string",
    "Integer": "integer",
    "Integer64": "integer64",
    "Decimal": "decimal",
    "Uri": "uri",
    "Url": "url",
    "Canonical": "canonical",
    "Code": "code",
    "Oid": "oid",
    "Id": "id",
    "Uuid": "uuid",
    "Markdown": "markdown",
    "UnsignedInt": "unsignedInt",
    "PositiveInt": "positiveInt",
    "Date": "date",
    "DateTime": "dateTime",
    "Time": "time",
    "Instant": "instant",
    "Base64Binary": "base64Binary",
    "Xhtml": "xhtml",
}

_FHIR_PRIMITIVES = set(_PRIMITIVE_MAP.values())

_COMMON_FIELDS = frozenset(
    {"id", "meta", "implicitRules", "language", "text", "contained", "extension", "modifierExtension"}
)


class _PydanticModel(Protocol):
    model_fields: dict[str, Any]


def _fhir_type_code(ann: Any) -> str:
    origin = getattr(ann, "__origin__", None)
    args = getattr(ann, "__args__", ())

    if origin is typing.Union or isinstance(ann, types.UnionType):
        for arg in args:
            if arg is type(None):
                continue
            return _fhir_type_code(arg)

    if origin is list:
        for arg in args:
            return _fhir_type_code(arg)

    if hasattr(ann, "__metadata__"):
        for m in ann.__metadata__:
            name = type(m).__name__
            return _PRIMITIVE_MAP.get(name, name)

    if ann is bool:
        return "boolean"

    name = getattr(ann, "__name__", str(ann))
    if name.endswith("Type"):
        name = name[:-4]
    if "." in name:
        name = name.rsplit(".", 1)[-1]
        if name.endswith("Type"):
            name = name[:-4]
    return _PRIMITIVE_MAP.get(name, name)


def _is_list_annotation(ann: Any) -> bool:
    s = str(ann)
    return "List[" in s or "list[" in s


def _is_backbone(cls: type) -> bool:
    return any(base.__name__ == "BackboneElement" for base in getattr(cls, "__mro__", []))


def _resolve_inner_class(ann: Any) -> type | None:
    origin = getattr(ann, "__origin__", None)
    args = getattr(ann, "__args__", ())
    if origin is typing.Union or isinstance(ann, types.UnionType):
        for arg in args:
            if arg is type(None):
                continue
            return _resolve_inner_class(arg)
    if origin is list:
        for arg in args:
            return _resolve_inner_class(arg)
    if isinstance(ann, type):
        get_model = getattr(ann, "get_model_klass", None)
        if callable(get_model):
            try:
                return cast(type, get_model())
            except (AttributeError, TypeError, ValueError):
                pass
        return ann
    return None


_building: set[int] = set()


def _build_children_from_model(cls: type) -> dict[str, ElementDef] | None:
    if not _is_backbone(cls):
        return None
    if not hasattr(cls, "model_fields"):
        return None
    cls_id = id(cls)
    if cls_id in _building:
        return None
    _building.add(cls_id)
    try:
        return _build_children_from_model_inner(cls)
    finally:
        _building.discard(cls_id)


def _build_children_from_model_inner(cls: type) -> dict[str, ElementDef] | None:
    children: dict[str, ElementDef] = {}
    model = cast(_PydanticModel, cls)
    for name, finfo in model.model_fields.items():
        extra = finfo.json_schema_extra or {}
        if not extra.get("element_property"):
            continue
        json_key = finfo.alias or name
        if json_key in _COMMON_FIELDS:
            continue
        type_code = _fhir_type_code(finfo.annotation)
        is_list = _is_list_annotation(finfo.annotation)
        is_required = extra.get("element_required", False)
        ref_types = extra.get("enum_reference_types")
        inner_cls = _resolve_inner_class(finfo.annotation)
        nested = _build_children_from_model(inner_cls) if inner_cls is not None else None
        if _is_backbone(inner_cls) if inner_cls else False:
            type_code = "BackboneElement"
        children[json_key] = ElementDef(
            types=(type_code,),
            min=1 if is_required else 0,
            max="*" if is_list else "1",
            target_types=tuple(sorted(ref_types)) if ref_types else (),
            children=nested,
        )
    return children if children else None


def _resource_def_from_model(model_cls: type) -> ResourceDef:
    elements: dict[str, ElementDef] = {}
    required: list[str] = []
    choice_groups: dict[str, list[str]] = {}
    model = cast(_PydanticModel, model_cls)

    for name, finfo in model.model_fields.items():
        extra = finfo.json_schema_extra or {}
        if not extra.get("element_property"):
            continue
        json_key = finfo.alias or name
        type_code = _fhir_type_code(finfo.annotation)
        is_list = _is_list_annotation(finfo.annotation)
        is_required = extra.get("element_required", False)
        is_modifier = json_key == "modifierExtension"
        ref_types = extra.get("enum_reference_types")
        one_of_many = extra.get("one_of_many")

        inner_cls = _resolve_inner_class(finfo.annotation)
        children = None
        if inner_cls is not None and _is_backbone(inner_cls):
            type_code = "BackboneElement"
            children = _build_children_from_model(inner_cls)

        required_binding = None
        enum_values = extra.get("enum_values")
        if isinstance(enum_values, list) and enum_values and type_code == "code":
            binding_key = f"_r4b_{json_key}_{id(model_cls)}"
            VALUE_SETS[binding_key] = set(enum_values)
            required_binding = binding_key

        elem = ElementDef(
            types=(type_code,),
            min=1 if is_required else 0,
            max="*" if is_list else "1",
            required_binding=required_binding,
            target_types=tuple(sorted(ref_types)) if ref_types else (),
            modifier=is_modifier,
            children=children,
        )
        elements[json_key] = elem

        if is_required and one_of_many is None:
            required.append(json_key)

        if one_of_many:
            choice_groups.setdefault(one_of_many, []).append(json_key)

    for _group_name, members in choice_groups.items():
        for member in members:
            current = elements.get(member)
            if current is not None:
                elements[member] = ElementDef(
                    types=current.types,
                    min=current.min,
                    max=current.max,
                    target_types=current.target_types,
                    modifier=current.modifier,
                    choices=tuple(members),
                    children=current.children,
                )

    return ResourceDef(required=tuple(sorted(set(required))), elements=elements)


@lru_cache(maxsize=1)
def _discover_r4b_models() -> dict[str, type]:
    import fhir.resources.R4B as r4b_pkg
    from fhir.resources.R4B.domainresource import DomainResource
    from fhir.resources.R4B.resource import Resource as FHIRResource

    models: dict[str, type] = {}
    for _importer, modname, ispkg in pkgutil.iter_modules(r4b_pkg.__path__):
        if ispkg or modname.startswith("_") or modname == "fhirtypes":
            continue
        try:
            mod = importlib.import_module(f"fhir.resources.R4B.{modname}")
        except ImportError:
            continue
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name, None)
            if (
                isinstance(obj, type)
                and issubclass(obj, (DomainResource, FHIRResource))
                and obj not in (DomainResource, FHIRResource)
                and hasattr(obj, "model_fields")
            ):
                try:
                    rt = obj.get_resource_type()
                except (AttributeError, TypeError, ValueError):
                    continue
                if isinstance(rt, str) and rt == attr_name:
                    models[rt] = obj
    return models


_cache: dict[str, ResourceDef] = {}


def r4_resource_def(resource_type: str) -> ResourceDef | None:
    if resource_type in _cache:
        return _cache[resource_type]
    models = _discover_r4b_models()
    model_cls = models.get(resource_type)
    if model_cls is None:
        return None
    result = _resource_def_from_model(model_cls)
    _cache[resource_type] = result
    return result


def r4_all_resource_types() -> frozenset[str]:
    return frozenset(_discover_r4b_models().keys())


COMMON_ELEMENTS: dict[str, ElementDef] = {
    "id": ElementDef(("id",)),
    "meta": ElementDef(("Meta",)),
    "implicitRules": ElementDef(("uri",)),
    "language": ElementDef(("code",)),
    "text": ElementDef(("Narrative",)),
    "contained": ElementDef(("Resource",), max="*"),
    "extension": ElementDef(("Extension",), max="*"),
    "modifierExtension": ElementDef(("Extension",), max="*", modifier=True),
}

R4_RESOURCES: dict[str, ResourceDef] = {}

COMPLEX_TYPE_FIELDS: dict[str, dict[str, tuple[type, ...]]] = {}

VALUE_SETS: dict[str, set[str]] = {}
