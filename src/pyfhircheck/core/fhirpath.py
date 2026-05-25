from __future__ import annotations

from functools import lru_cache
import re
from typing import Any, Callable

from pyfhircheck.core.util import get_path, has_path

_CHOICE_SUFFIXES = (
    "Quantity", "CodeableConcept", "String", "Boolean", "Integer", "DateTime",
    "Date", "Time", "Instant", "Period", "Range", "Ratio", "SampledData",
    "Age", "Duration", "Timing", "Annotation", "Attachment", "Identifier",
    "Reference", "Coding", "HumanName", "Address", "ContactPoint", "Money",
    "Count", "Distance", "Signature", "Dosage", "Decimal", "Uri", "Url",
    "Canonical", "Uuid", "Id", "Oid", "Markdown", "Base64Binary",
    "PositiveInt", "UnsignedInt", "Code",
)


def _augment_choice_types(data: Any) -> Any:
    if isinstance(data, dict):
        result = {k: _augment_choice_types(v) for k, v in data.items()}
        for key in list(data):
            for suffix in _CHOICE_SUFFIXES:
                if key.endswith(suffix) and len(key) > len(suffix) and key[0].islower():
                    base = key[: -len(suffix)]
                    if base not in result:
                        result[base] = result[key]
                    break
        return result
    if isinstance(data, list):
        return [_augment_choice_types(item) for item in data]
    return data


EXISTS_RE = re.compile(r"^([A-Za-z][A-Za-z0-9.]*)\.exists\(\)$")
EMPTY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9.]*)\.empty\(\)$")
EQUALS_RE = re.compile(r"^([A-Za-z][A-Za-z0-9.]*)\s*=\s*'([^']*)'$")
IMPLIES_RE = re.compile(r"^(.+)\s+implies\s+(.+)$")


def evaluate(resource: dict[str, Any], expression: str) -> bool | None:
    augmented = _augment_choice_types(resource)
    backend_result = _evaluate_with_fhirpathpy(augmented, expression)
    if backend_result is not None:
        return backend_result
    return _evaluate_fallback(augmented, expression)


def backend_name() -> str:
    return "fhirpathpy" if _fhirpathpy_available() else "fallback"


def _evaluate_with_fhirpathpy(resource: dict[str, Any], expression: str) -> bool | None:
    if not _fhirpathpy_available():
        return None
    try:
        compiled = _compile(expression)
        result = compiled(resource, {})
    except Exception:
        return None
    return _coerce_result(result)


@lru_cache(maxsize=2048)
def _compile(expression: str) -> Callable[[dict[str, Any], dict[str, Any]], Any]:
    from fhirpathpy import compile as compile_fhirpath

    return compile_fhirpath(expression)


@lru_cache(maxsize=1)
def _fhirpathpy_available() -> bool:
    try:
        import fhirpathpy  # noqa: F401
    except ImportError:
        return False
    return True


def _coerce_result(result: Any) -> bool | None:
    if isinstance(result, list):
        if len(result) == 1 and isinstance(result[0], bool):
            return result[0]
        if len(result) == 0:
            return False
        return all(bool(item) for item in result)
    if isinstance(result, bool):
        return result
    return None


def _evaluate_fallback(resource: dict[str, Any], expression: str) -> bool | None:
    expression = expression.strip()
    if expression in {"true", "True"}:
        return True
    if expression in {"false", "False"}:
        return False
    implies = IMPLIES_RE.match(expression)
    if implies:
        left = _evaluate_fallback(resource, implies.group(1).strip())
        right = _evaluate_fallback(resource, implies.group(2).strip())
        if left is None or right is None:
            return None
        return (not left) or right
    if " or " in expression:
        values = [_evaluate_fallback(resource, part.strip()) for part in expression.split(" or ")]
        return None if any(value is None for value in values) else any(values)
    if " and " in expression:
        values = [_evaluate_fallback(resource, part.strip()) for part in expression.split(" and ")]
        return None if any(value is None for value in values) else all(values)
    exists = EXISTS_RE.match(expression)
    if exists:
        value = get_path(resource, _strip_resource_prefix(resource, exists.group(1)))
        return value not in (None, [], "")
    empty = EMPTY_RE.match(expression)
    if empty:
        value = get_path(resource, _strip_resource_prefix(resource, empty.group(1)))
        return value in (None, [], "")
    equals = EQUALS_RE.match(expression)
    if equals:
        return bool(get_path(resource, _strip_resource_prefix(resource, equals.group(1))) == equals.group(2))
    if re.match(r"^[A-Za-z][A-Za-z0-9.]*$", expression):
        return has_path(resource, _strip_resource_prefix(resource, expression))
    return None


def _strip_resource_prefix(resource: dict[str, Any], path: str) -> str:
    resource_type = resource.get("resourceType")
    prefix = f"{resource_type}."
    if isinstance(resource_type, str) and path.startswith(prefix):
        return path[len(prefix) :]
    return path
