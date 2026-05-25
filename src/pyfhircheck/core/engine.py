from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pyfhircheck import __version__
from pyfhircheck.config import ValidatorConfig
from pyfhircheck.core.definitions import R4_RESOURCES, ElementDef, r4_all_resource_types, r4_resource_def
from pyfhircheck.core.fhirpath import backend_name, evaluate
from pyfhircheck.core.util import DATE_RE, DATETIME_RE, FHIR_REF_RE, ID_RE, INSTANT_RE, file_sha256, iter_json_files, load_json_file, resource_key, stable_hash, values_at_path
from pyfhircheck.models import ResourceValidationSummary, Severity, Status, ValidationIssue, ValidationReport
from pyfhircheck.observability import get_logger
from pyfhircheck.profiles.loader import ProfileRegistry
from pyfhircheck.profiles.package import PackageResolver
from pyfhircheck.profiles.specification import PRIMITIVE_TYPES, SpecificationDefinitions, merged_complex_types
from pyfhircheck.rules.custom import CustomRuleRunner
from pyfhircheck.terminology.resolver import TerminologyResolver


def _matches_pattern(value: Any, pattern: Any) -> bool:
    if isinstance(pattern, dict) and isinstance(value, dict):
        return all(_matches_pattern(value.get(key), expected) for key, expected in pattern.items())
    if isinstance(pattern, list) and isinstance(value, list):
        if len(value) < len(pattern):
            return False
        return all(_matches_pattern(actual, expected) for actual, expected in zip(value, pattern, strict=False))
    return bool(value == pattern)


def _contained_reference_targets(resource: dict[str, Any]) -> dict[str, str]:
    contained = resource.get("contained", [])
    if not isinstance(contained, list):
        return {}
    return {
        f"#{child['id']}": child["resourceType"]
        for child in contained
        if isinstance(child, dict) and isinstance(child.get("id"), str) and isinstance(child.get("resourceType"), str)
    }


def _reference_resource_type(reference: str) -> str | None:
    if reference.startswith("#"):
        return None
    path = urlparse(reference).path if reference.startswith(("http://", "https://")) else reference
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[-2] == "_history" and len(parts) >= 4:
        return parts[-4]
    if len(parts) >= 2:
        return parts[-2]
    return None


def _reference_local_key(reference: str) -> str:
    if reference.startswith(("http://", "https://")):
        path = urlparse(reference).path
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "_history" and len(parts) >= 4:
            return f"{parts[-4]}/{parts[-3]}"
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
    return reference.split("/_history/", 1)[0]


def _is_conditional_reference(reference: str) -> bool:
    return "?" in reference and "/" in reference


VALUE_FIELD_TYPES = {
    "valueString": "string",
    "valueBoolean": "boolean",
    "valueCode": "code",
    "valueUri": "uri",
    "valueUrl": "url",
    "valueCanonical": "canonical",
    "valueInteger": "integer",
    "valueDecimal": "decimal",
    "valueDate": "date",
    "valueDateTime": "dateTime",
    "valueInstant": "instant",
    "valueCoding": "Coding",
    "valueCodeableConcept": "CodeableConcept",
    "valueIdentifier": "Identifier",
    "valueQuantity": "Quantity",
    "valueReference": "Reference",
    "valuePeriod": "Period",
}

_CHOICE_SUFFIX_TO_FHIR_TYPE: dict[str, str] = {
    "String": "string", "Boolean": "boolean", "Code": "code",
    "Uri": "uri", "Url": "url", "Canonical": "canonical",
    "Integer": "integer", "Decimal": "decimal", "Date": "date",
    "DateTime": "dateTime", "Instant": "instant", "Time": "time",
    "Base64Binary": "base64Binary", "Markdown": "markdown",
    "Id": "id", "Oid": "oid", "Uuid": "uuid",
    "UnsignedInt": "unsignedInt", "PositiveInt": "positiveInt",
}


_SKIP_INVARIANTS = {"ext-1", "ele-1", "dom-6", "con-1", "bdl-5", "vsd-6"}

logger = get_logger("engine")


def _codeable_concept_codes(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    codings = value.get("coding")
    if not isinstance(codings, list):
        return []
    return [c["code"] for c in codings if isinstance(c, dict) and isinstance(c.get("code"), str)]


def _choice_field_present(resource: dict[str, Any], field: str) -> bool:
    if "[x]" not in field:
        return field in resource and resource[field] not in (None, "", [])
    prefix = field.replace("[x]", "")
    return any(
        k.startswith(prefix) and k != prefix and len(k) > len(prefix) and k[len(prefix)].isupper()
        for k in resource
    )


class Validator:
    def __init__(self, config: ValidatorConfig | None = None):
        self.config = config or ValidatorConfig()
        self.resolved_packages = PackageResolver(self.config.package_cache_dir).resolve_with_dependencies(self.config.packages)
        package_paths = [package.path for package in self.resolved_packages]
        all_local_package_paths = [*package_paths, *self.config.local_package_paths]
        self.profile_registry = ProfileRegistry()
        self.profile_registry.load_paths(all_local_package_paths)
        self.profile_registry.load_remote_sources(self.config.remote_package_sources)
        specification = SpecificationDefinitions.load(all_local_package_paths, self.config.remote_package_sources)
        self.specification = specification
        self.resource_definitions = {**R4_RESOURCES, **specification.resources}
        self.complex_type_fields = merged_complex_types(specification.complex_types)
        self.complex_type_elements = specification.complex_type_elements
        self.loaded_structure_definitions = specification.loaded_structure_definitions
        self.merged_snapshots = specification.merged_snapshots + self.profile_registry.merged_snapshots
        self.terminology = TerminologyResolver(self.config.terminology, all_local_package_paths, self.config.remote_package_sources)
        self.custom_rules = CustomRuleRunner(self.config.custom_rules)
        logger.info(
            "validator initialized",
            extra={
                "package_count": len(self.resolved_packages),
                "structure_definitions": self.loaded_structure_definitions,
                "merged_snapshots": self.merged_snapshots,
                "resource_definitions": len(self.resource_definitions),
                "fhirpath_backend": backend_name(),
            },
        )

    def validate_path(self, path: Path, input_source: str | None = None) -> ValidationReport:
        return self.validate_files(iter_json_files(path), input_source or str(path))

    def validate_files(
        self,
        file_paths: list[Path],
        input_source: str = "files",
        reference_file_paths: list[Path] | None = None,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        resources: list[dict[str, Any]] = []
        validation_resources: list[dict[str, Any]] = []
        input_payloads: list[Any] = []
        input_hashes: dict[str, str] = {}
        reference_index = self._reference_index(reference_file_paths or [])
        for file_path in sorted(file_paths):
            try:
                input_hashes[str(file_path)] = file_sha256(file_path)
            except OSError:
                pass
            data, error = load_json_file(file_path)
            if error:
                issues.append(ValidationIssue(Severity.ERROR, "json.invalid", f"Invalid JSON: {error}", path=str(file_path), source="json"))
                continue
            input_payloads.append(data)
            if isinstance(data, dict) and data.get("resourceType") == "Bundle":
                bundle_resources, bundle_issues, bundle_index = self._validate_bundle(data)
                resources.extend(bundle_resources)
                issues.extend(self._validate_resources(bundle_resources[1:], bundle_index, bundle_entry=True))
                issues.extend(bundle_issues)
            elif isinstance(data, dict):
                resources.append(data)
                validation_resources.append(data)
            else:
                issues.append(ValidationIssue(Severity.ERROR, "json.not-object", "FHIR resource JSON must be an object", path=str(file_path), source="json"))
        issues.extend(self._validate_resources(validation_resources, reference_index))
        return self._report(issues, resources, input_source, input_payloads, input_hashes)

    def _reference_index(self, file_paths: list[Path]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for file_path in sorted(file_paths):
            data, error = load_json_file(file_path)
            if error or not isinstance(data, dict):
                continue
            candidates = [data]
            if data.get("resourceType") == "Bundle" and isinstance(data.get("entry"), list):
                candidates.extend(
                    resource
                    for entry in data["entry"]
                    if isinstance(entry, dict)
                    for resource in (entry.get("resource"),)
                    if isinstance(resource, dict)
                )
            for resource in candidates:
                if isinstance(resource, dict) and (key := resource_key(resource)):
                    index[key] = resource
        return index

    def validate_resource(self, resource: dict[str, Any], input_source: str = "memory") -> ValidationReport:
        if resource.get("resourceType") == "Bundle":
            resources, issues, bundle_index = self._validate_bundle(resource)
            issues.extend(self._validate_resources(resources[1:], bundle_index, bundle_entry=True))
        else:
            resources = [resource]
            issues = self._validate_resources(resources)
        return self._report(issues, resources, input_source, [resource], {})

    def validate_server(self, base_url: str) -> ValidationReport:
        import json
        from urllib.error import HTTPError, URLError
        from urllib.request import urlopen

        targets = self.config.server_validation_targets or ["Patient"]
        resources: list[dict[str, Any]] = []
        validation_resources: list[dict[str, Any]] = []
        issues: list[ValidationIssue] = []
        payloads: list[Any] = []
        for resource_type in targets:
            url = f"{base_url.rstrip('/')}/{resource_type}"
            seen_pages: set[str] = set()
            page_url: str | None = url
            while page_url and page_url not in seen_pages:
                seen_pages.add(page_url)
                try:
                    with urlopen(page_url, timeout=15) as response:
                        data = json.loads(response.read().decode("utf-8"))
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    issues.append(ValidationIssue(Severity.ERROR, "server.fetch", f"Could not fetch {page_url}: {exc}", resource_type, path=page_url, source="server"))
                    break
                payloads.append(data)
                if isinstance(data, dict) and data.get("resourceType") == "Bundle":
                    bundle_resources, bundle_issues, bundle_index = self._validate_bundle(data)
                    resources.extend(bundle_resources)
                    issues.extend(self._validate_resources(bundle_resources[1:], bundle_index, bundle_entry=True))
                    issues.extend(bundle_issues)
                    page_url = self._next_link(data)
                elif isinstance(data, dict):
                    resources.append(data)
                    validation_resources.append(data)
                    break
        issues.extend(self._validate_resources(validation_resources))
        return self._report(issues, resources, base_url, payloads, {})

    def _next_link(self, bundle: dict[str, Any]) -> str | None:
        for link in bundle.get("link", []):
            if not isinstance(link, dict) or link.get("relation") != "next":
                continue
            url = link.get("url")
            if isinstance(url, str):
                return url
        return None

    def _validate_bundle(self, bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[ValidationIssue], dict[str, dict[str, Any]]]:
        issues = self._validate_one(bundle, {})
        issues.extend(self.custom_rules.validate_bundle(bundle))
        entries = bundle.get("entry", [])
        resources: list[dict[str, Any]] = [bundle]
        if not isinstance(entries, list):
            issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.type", "Bundle.entry must be an array", "Bundle", bundle.get("id"), "Bundle.entry", source="bundle"))
            return resources, issues, {}
        full_urls: list[str] = []
        index: dict[str, dict[str, Any]] = {}
        bundle_type = bundle.get("type")
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.object", "Bundle.entry items must be objects", "Bundle", bundle.get("id"), f"Bundle.entry[{idx}]", source="bundle"))
                continue
            issues.extend(self._validate_bundle_entry(bundle, entry, idx, bundle_type))
            full_url = entry.get("fullUrl")
            if isinstance(full_url, str):
                full_urls.append(full_url)
            resource = entry.get("resource")
            if not isinstance(resource, dict):
                if bundle_type not in {"transaction-response", "batch-response"}:
                    issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.resource", "Bundle.entry.resource is required", "Bundle", bundle.get("id"), f"Bundle.entry[{idx}].resource", source="bundle"))
                continue
            resources.append(resource)
            key = resource_key(resource)
            if key:
                index[key] = resource
            if isinstance(full_url, str):
                index[full_url] = resource
                if key and full_url.endswith(key) is False and not full_url.startswith("urn:uuid:"):
                    issues.append(ValidationIssue(Severity.WARNING, "bundle.fullurl.mismatch", "Bundle.entry.fullUrl does not match resource type/id", resource.get("resourceType"), resource.get("id"), f"Bundle.entry[{idx}].fullUrl", source="bundle"))
        duplicates = [url for url, count in Counter(full_urls).items() if count > 1]
        for url in duplicates:
            issues.append(ValidationIssue(Severity.ERROR, "bundle.fullurl.duplicate", f"Duplicate Bundle.entry.fullUrl {url}", "Bundle", bundle.get("id"), "Bundle.entry.fullUrl", source="bundle"))
        if bundle_type == "document":
            first = resources[1] if len(resources) > 1 else None
            if not isinstance(first, dict) or first.get("resourceType") != "Composition":
                issues.append(ValidationIssue(Severity.ERROR, "bundle.document.composition", "document Bundle first entry must be a Composition", "Bundle", bundle.get("id"), "Bundle.entry[0].resource", source="bundle"))
        if bundle_type == "message":
            first = resources[1] if len(resources) > 1 else None
            if not isinstance(first, dict) or first.get("resourceType") != "MessageHeader":
                issues.append(ValidationIssue(Severity.ERROR, "bundle.message.header", "message Bundle first entry must be a MessageHeader", "Bundle", bundle.get("id"), "Bundle.entry[0].resource", source="bundle"))
        return resources, issues, index

    def _validate_bundle_entry(self, bundle: dict[str, Any], entry: dict[str, Any], idx: int, bundle_type: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        bundle_id = bundle.get("id")
        path = f"Bundle.entry[{idx}]"
        request = entry.get("request")
        response = entry.get("response")
        search = entry.get("search")
        if bundle_type in {"transaction", "batch", "history"}:
            if not isinstance(request, dict):
                issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.request", f"{bundle_type} Bundle entries require request", "Bundle", bundle_id, f"{path}.request", source="bundle"))
            else:
                method = request.get("method")
                url = request.get("url")
                allowed_methods = {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"}
                if method not in allowed_methods:
                    issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.request.method", "Bundle.entry.request.method is invalid or missing", "Bundle", bundle_id, f"{path}.request.method", source="bundle"))
                if not isinstance(url, str) or not url:
                    issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.request.url", "Bundle.entry.request.url is required", "Bundle", bundle_id, f"{path}.request.url", source="bundle"))
                if bundle_type == "history" and method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    issues.append(ValidationIssue(Severity.ERROR, "bundle.history.method", "history Bundle request.method must be a history event method", "Bundle", bundle_id, f"{path}.request.method", source="bundle"))
        if bundle_type in {"transaction-response", "batch-response", "history"}:
            if not isinstance(response, dict):
                issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.response", f"{bundle_type} Bundle entries require response", "Bundle", bundle_id, f"{path}.response", source="bundle"))
            elif not isinstance(response.get("status"), str) or not response.get("status"):
                issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.response.status", "Bundle.entry.response.status is required", "Bundle", bundle_id, f"{path}.response.status", source="bundle"))
        if bundle_type == "searchset":
            if "total" not in bundle:
                issues.append(ValidationIssue(Severity.WARNING, "bundle.searchset.total", "searchset Bundle should include total", "Bundle", bundle_id, "Bundle.total", source="bundle"))
            if search is not None:
                if not isinstance(search, dict):
                    issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.search", "Bundle.entry.search must be an object", "Bundle", bundle_id, f"{path}.search", source="bundle"))
                elif search.get("mode") not in {None, "match", "include", "outcome"}:
                    issues.append(ValidationIssue(Severity.ERROR, "bundle.entry.search.mode", "Bundle.entry.search.mode is invalid", "Bundle", bundle_id, f"{path}.search.mode", source="bundle"))
        if bundle_type not in {"transaction", "batch", "history"} and request is not None:
            issues.append(ValidationIssue(Severity.WARNING, "bundle.entry.request.unexpected", f"{bundle_type} Bundle entries should not include request", "Bundle", bundle_id, f"{path}.request", source="bundle"))
        if bundle_type not in {"transaction-response", "batch-response", "history"} and response is not None:
            issues.append(ValidationIssue(Severity.WARNING, "bundle.entry.response.unexpected", f"{bundle_type} Bundle entries should not include response", "Bundle", bundle_id, f"{path}.response", source="bundle"))
        return issues

    def _validate_resources(self, resources: list[dict[str, Any]], index: dict[str, dict[str, Any]] | None = None, *, bundle_entry: bool = False) -> list[ValidationIssue]:
        index = {**(index or {}), **{key: resource for resource in resources if (key := resource_key(resource))}}
        issues: list[ValidationIssue] = []
        seen: set[str] = set()
        for resource in resources:
            key = resource_key(resource)
            if key and key in seen:
                issues.append(ValidationIssue(Severity.ERROR, "resource.id.duplicate", f"Duplicate resource id {key}", resource.get("resourceType"), resource.get("id"), "id", source="structure"))
            if key:
                seen.add(key)
            issues.extend(self._validate_one(resource, index, bundle_entry=bundle_entry))
            issues.extend(self.custom_rules.validate(resource, index))
        return issues

    def _validate_one(self, resource: dict[str, Any], index: dict[str, dict[str, Any]], *, bundle_entry: bool = False) -> list[ValidationIssue]:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        issues: list[ValidationIssue] = []
        if not isinstance(resource_type, str):
            return [ValidationIssue(Severity.ERROR, "resourceType.required", "resourceType is required", path="resourceType", source="structure")]
        definition = self.resource_definitions.get(resource_type)
        if definition is None:
            definition = self.specification.resource_def_for(resource_type)
            if definition is not None:
                self.resource_definitions[resource_type] = definition
        if definition is None:
            definition = r4_resource_def(resource_type)
            if definition is not None:
                self.resource_definitions[resource_type] = definition
        if definition is None:
            return [ValidationIssue(Severity.ERROR, "resourceType.unknown", f"Unknown or unsupported R4 resourceType {resource_type}", resource_type, resource_id, "resourceType", source="structure")]
        contained_refs = _contained_reference_targets(resource)
        for field in definition.required:
            if not _choice_field_present(resource, field):
                issues.append(ValidationIssue(Severity.ERROR, "cardinality.min", f"{resource_type}.{field} is required", resource_type, resource_id, f"{resource_type}.{field}", source="structure"))
        issues.extend(self._validate_choice_groups(resource, definition))
        for field, value in resource.items():
            if field == "resourceType" or field.startswith("_"):
                continue
            element = definition.elements.get(field)
            path = f"{resource_type}.{field}"
            if element is None:
                issues.append(ValidationIssue(Severity.ERROR, "element.unknown", f"Unknown element {path}", resource_type, resource_id, path, source="structure"))
                continue
            issues.extend(self._validate_element(resource_type, resource_id, path, value, element, index, contained_refs))
        issues.extend(self._validate_meta_profiles(resource, definition, bundle_entry=bundle_entry))
        issues.extend(self._validate_contained(resource))
        return issues

    def _validate_choice_groups(self, resource: dict[str, Any], definition: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        checked: set[tuple[str, ...]] = set()
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        for field, element in definition.elements.items():
            if not element.choices or element.choices in checked:
                continue
            checked.add(element.choices)
            present = [choice for choice in element.choices if choice in resource and resource[choice] not in (None, "", [])]
            if len(present) > 1:
                issues.append(ValidationIssue(Severity.ERROR, "choice.multiple", f"Only one of {', '.join(element.choices)} may be present", resource_type, resource_id, f"{resource_type}.{field}", source="structure"))
        return issues

    def _validate_element(self, resource_type: str, resource_id: str | None, path: str, value: Any, element: ElementDef, index: dict[str, dict[str, Any]], contained_refs: dict[str, str] | None = None) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        values = value if isinstance(value, list) else [value]
        if element.max != "*" and isinstance(value, list):
            issues.append(ValidationIssue(Severity.ERROR, "cardinality.max", f"{path} must not repeat", resource_type, resource_id, path, source="structure"))
        if element.min > 0 and len(values) < element.min:
            issues.append(ValidationIssue(Severity.ERROR, "cardinality.min", f"{path} requires at least {element.min} value(s)", resource_type, resource_id, path, source="structure"))
        if element.modifier and value:
            issues.append(ValidationIssue(Severity.WARNING, "modifierExtension.present", "modifierExtension is present and must be reviewed by consumers", resource_type, resource_id, path, source="metadata"))
        for idx, item in enumerate(values):
            item_path = f"{path}[{idx}]" if isinstance(value, list) else path
            issues.extend(self._validate_type(resource_type, resource_id, item_path, item, element, index, contained_refs or {}))
            item_codes = [item] if isinstance(item, str) else _codeable_concept_codes(item)
            if element.required_binding:
                for code in item_codes:
                    contains = self.terminology.contains(element.required_binding, code)
                    if contains is False:
                        issues.append(ValidationIssue(Severity.ERROR, "terminology.required", f"Code {code!r} is not in required ValueSet {element.required_binding}", resource_type, resource_id, item_path, source="terminology"))
            if element.extensible_binding:
                for code in item_codes:
                    contains = self.terminology.contains(element.extensible_binding, code)
                    if contains is False:
                        issues.append(ValidationIssue(Severity.WARNING, "terminology.extensible", f"Code {code!r} is not in extensible ValueSet {element.extensible_binding}", resource_type, resource_id, item_path, source="terminology"))
        return issues

    def _validate_type(self, resource_type: str, resource_id: str | None, path: str, value: Any, element: ElementDef, index: dict[str, dict[str, Any]], contained_refs: dict[str, str]) -> list[ValidationIssue]:
        expected = element.types
        issues: list[ValidationIssue] = []
        primitive = expected[0]
        ok = True
        if primitive in {"string", "code", "uri", "canonical", "url", "markdown", "base64Binary", "oid", "uuid", "xhtml"}:
            ok = isinstance(value, str)
        elif primitive == "instant":
            ok = isinstance(value, str) and bool(INSTANT_RE.match(value))
        elif primitive == "boolean":
            ok = isinstance(value, bool)
        elif primitive in {"integer", "positiveInt", "unsignedInt"}:
            ok = isinstance(value, int) and not isinstance(value, bool)
            if ok and primitive == "positiveInt" and value < 1:
                issues.append(ValidationIssue(Severity.ERROR, "datatype.invalid", f"{path} must be a positive integer", resource_type, resource_id, path, source="datatype"))
            if ok and primitive == "unsignedInt" and value < 0:
                issues.append(ValidationIssue(Severity.ERROR, "datatype.invalid", f"{path} must be a non-negative integer", resource_type, resource_id, path, source="datatype"))
        elif primitive == "decimal":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif primitive == "id":
            ok = isinstance(value, str) and bool(ID_RE.match(value))
        elif primitive == "date":
            ok = isinstance(value, str) and bool(DATE_RE.match(value))
        elif primitive == "dateTime":
            ok = isinstance(value, str) and bool(DATETIME_RE.match(value))
        elif primitive == "time":
            ok = isinstance(value, str)
        elif primitive == "Reference":
            ok = isinstance(value, dict)
            ref = value.get("reference") if ok else None
            declared_type = value.get("type") if ok else None
            if isinstance(declared_type, str) and element.target_types and declared_type.rsplit("/", 1)[-1] not in element.target_types:
                issues.append(ValidationIssue(Severity.ERROR, "reference.type", f"Reference.type {declared_type} does not match allowed target type(s) {', '.join(element.target_types)}", resource_type, resource_id, path, source="reference"))
            if ok:
                issues.extend(self._validate_complex_type(resource_type, resource_id, path, value, "Reference"))
            if isinstance(ref, str):
                if ref.startswith("#"):
                    if ref not in contained_refs:
                        issues.append(ValidationIssue(Severity.ERROR, "reference.contained.unresolved", f"Contained reference {ref} does not resolve", resource_type, resource_id, path, source="reference"))
                    elif element.target_types and contained_refs[ref] not in element.target_types:
                        issues.append(ValidationIssue(Severity.ERROR, "reference.type", f"Contained reference {ref} does not match allowed target type(s) {', '.join(element.target_types)}", resource_type, resource_id, path, source="reference"))
                    return issues
                if _is_conditional_reference(ref):
                    return issues
                if not (FHIR_REF_RE.match(ref) or ref.startswith(("http://", "https://", "urn:uuid:", "urn:oid:"))):
                    issues.append(ValidationIssue(Severity.ERROR, "reference.format", f"Invalid reference {ref}", resource_type, resource_id, path, source="reference"))
                ref_type = _reference_resource_type(ref)
                if element.target_types and ref_type is not None and ref_type not in element.target_types:
                    issues.append(ValidationIssue(Severity.ERROR, "reference.type", f"Reference {ref} does not match allowed target type(s) {', '.join(element.target_types)}", resource_type, resource_id, path, source="reference"))
                local_key = _reference_local_key(ref)
                if ref.startswith(("http://", "https://")) and index and ref not in index and local_key not in index:
                    issues.append(ValidationIssue(Severity.WARNING, "reference.external", f"External reference {ref} was not resolved locally", resource_type, resource_id, path, source="reference"))
                elif ref and not ref.startswith(("http://", "https://", "urn:oid:")) and ref not in index and local_key not in index and index:
                    issues.append(ValidationIssue(Severity.WARNING, "reference.unresolved", f"Reference {ref} does not resolve in local validation set", resource_type, resource_id, path, source="reference"))
        elif primitive == "Resource":
            ok = isinstance(value, dict) and isinstance(value.get("resourceType"), str)
        elif primitive == "Extension":
            ok = isinstance(value, dict)
            if ok:
                issues.extend(self._validate_extension(resource_type, resource_id, path, value, element.modifier))
        elif primitive == "BackboneElement" and element.children and isinstance(value, dict):
            children = self._merged_backbone_children(resource_type, path, element.children)
            issues.extend(self._validate_backbone(resource_type, resource_id, path, value, children, index, contained_refs))
        else:
            ok = isinstance(value, dict)
            if ok:
                issues.extend(self._validate_complex_type(resource_type, resource_id, path, value, primitive))
        if not ok:
            issues.append(ValidationIssue(Severity.ERROR, "datatype.invalid", f"{path} must be {', '.join(expected)}", resource_type, resource_id, path, source="datatype"))
        return issues

    def _merged_backbone_children(self, resource_type: str, path: str, children: dict[str, ElementDef]) -> dict[str, ElementDef]:
        r4_def = r4_resource_def(resource_type)
        if r4_def is None:
            return children
        field_name = path.split(".")[-1] if "." in path else path.replace(f"{resource_type}.", "")
        r4_element = r4_def.elements.get(field_name)
        if r4_element is None or r4_element.children is None:
            return children
        merged = {k: v for k, v in r4_element.children.items() if k not in ("id", "extension", "modifierExtension")}
        merged.update(children)
        return merged

    def _validate_backbone(self, resource_type: str, resource_id: str | None, path: str, value: dict[str, Any], children: dict[str, ElementDef], index: dict[str, dict[str, Any]], contained_refs: dict[str, str]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for child_name, child_def in children.items():
            child_path = f"{path}.{child_name}"
            if child_def.min > 0 and (child_name not in value or value[child_name] in (None, "", [])):
                issues.append(ValidationIssue(Severity.ERROR, "cardinality.min", f"{child_path} is required", resource_type, resource_id, child_path, source="structure"))
        for field_name, field_value in value.items():
            if field_name in ("id", "extension", "modifierExtension"):
                continue
            field_path = f"{path}.{field_name}"
            field_def = children.get(field_name)
            if field_def is None:
                issues.append(ValidationIssue(Severity.ERROR, "element.unknown", f"Unknown element {field_path}", resource_type, resource_id, field_path, source="structure"))
                continue
            items = field_value if isinstance(field_value, list) else [field_value]
            if field_def.max != "*" and isinstance(field_value, list):
                issues.append(ValidationIssue(Severity.ERROR, "cardinality.max", f"{field_path} must not repeat", resource_type, resource_id, field_path, source="structure"))
            for idx, item in enumerate(items):
                item_path = f"{field_path}[{idx}]" if isinstance(field_value, list) else field_path
                issues.extend(self._validate_type(resource_type, resource_id, item_path, item, field_def, index, contained_refs))
                child_codes = [item] if isinstance(item, str) else _codeable_concept_codes(item)
                if field_def.required_binding:
                    for bound_code in child_codes:
                        contains = self.terminology.contains(field_def.required_binding, bound_code)
                        if contains is False:
                            issues.append(ValidationIssue(Severity.ERROR, "terminology.required", f"Code {bound_code!r} is not in required ValueSet {field_def.required_binding}", resource_type, resource_id, item_path, source="terminology"))
                if field_def.extensible_binding:
                    for bound_code in child_codes:
                        contains = self.terminology.contains(field_def.extensible_binding, bound_code)
                        if contains is False:
                            issues.append(ValidationIssue(Severity.WARNING, "terminology.extensible", f"Code {bound_code!r} is not in extensible ValueSet {field_def.extensible_binding}", resource_type, resource_id, item_path, source="terminology"))
        return issues

    def _validate_complex_type(self, resource_type: str, resource_id: str | None, path: str, value: dict[str, Any], type_name: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        known_fields = self.complex_type_fields.get(type_name)
        if known_fields is None:
            return issues
        type_elements = self.complex_type_elements.get(type_name, {})
        for field_name, item in value.items():
            if field_name.startswith(("extension", "_")):
                continue
            expected = known_fields.get(field_name)
            child_path = f"{path}.{field_name}"
            if expected is None:
                if field_name in {"id", "modifierExtension"}:
                    continue
                issues.append(ValidationIssue(Severity.WARNING, "datatype.unknown-field", f"Unknown field {field_name} in {type_name}", resource_type, resource_id, child_path, source="datatype"))
                continue
            if not isinstance(item, expected) and not (isinstance(item, list) and all(isinstance(i, expected) for i in item)):
                expected_names = ", ".join(t.__name__ for t in expected)
                issues.append(ValidationIssue(Severity.ERROR, "datatype.invalid", f"{child_path} must be {expected_names}", resource_type, resource_id, child_path, source="datatype"))
            elem_def = type_elements.get(field_name)
            if elem_def is not None:
                items = item if isinstance(item, list) else [item]
                for idx, sub_item in enumerate(items):
                    item_path = f"{child_path}[{idx}]" if isinstance(item, list) else child_path
                    sub_codes = [sub_item] if isinstance(sub_item, str) else _codeable_concept_codes(sub_item)
                    if elem_def.required_binding:
                        for bound_code in sub_codes:
                            contains = self.terminology.contains(elem_def.required_binding, bound_code)
                            if contains is False:
                                issues.append(ValidationIssue(Severity.ERROR, "terminology.required", f"Code {bound_code!r} is not in required ValueSet {elem_def.required_binding}", resource_type, resource_id, item_path, source="terminology"))
                    if elem_def.extensible_binding:
                        for bound_code in sub_codes:
                            contains = self.terminology.contains(elem_def.extensible_binding, bound_code)
                            if contains is False:
                                issues.append(ValidationIssue(Severity.WARNING, "terminology.extensible", f"Code {bound_code!r} is not in extensible ValueSet {elem_def.extensible_binding}", resource_type, resource_id, item_path, source="terminology"))
                    child_type = elem_def.types[0] if elem_def.types else None
                    if child_type and child_type not in PRIMITIVE_TYPES and isinstance(sub_item, dict) and child_type in self.complex_type_fields:
                        issues.extend(self._validate_complex_type(resource_type, resource_id, item_path, sub_item, child_type))
        if type_name == "Coding":
            coding_system = value.get("system")
            coding_code = value.get("code")
            if isinstance(coding_system, str) and isinstance(coding_code, str):
                valid = self.terminology.validate_coding(coding_system, coding_code)
                if valid is False:
                    issues.append(ValidationIssue(Severity.ERROR, "terminology.code-system", f"Code {coding_code!r} is not valid in code system {coding_system}", resource_type, resource_id, path, source="terminology"))
        if type_name == "Period" and isinstance(value.get("start"), str) and isinstance(value.get("end"), str) and value["start"] > value["end"]:
            issues.append(ValidationIssue(Severity.ERROR, "invariant.period.order", "Period.start must not be after Period.end", resource_type, resource_id, path, source="invariant"))
        return issues

    def _validate_extension(self, resource_type: str, resource_id: str | None, path: str, value: dict[str, Any], modifier_context: bool = False) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        extension_url = value.get("url")
        if not isinstance(extension_url, str) or not extension_url:
            issues.append(ValidationIssue(Severity.ERROR, "extension.url", "Extension.url is required", resource_type, resource_id, f"{path}.url", source="extension"))
            extension_definition = None
        else:
            extension_definition = self.profile_registry.get_extension(extension_url)
            if extension_definition is None and not self.config.allow_unknown_extensions:
                issues.append(ValidationIssue(Severity.WARNING, "extension.unknown", f"Extension definition {extension_url} is not loaded", resource_type, resource_id, path, source="extension"))
        value_fields = [field for field in value if field.startswith("value")]
        if len(value_fields) > 1:
            issues.append(ValidationIssue(Severity.ERROR, "extension.value.multiple", "Extension may contain only one value[x]", resource_type, resource_id, path, source="extension"))
        if "extension" in value and value_fields:
            issues.append(ValidationIssue(Severity.ERROR, "extension.nested-value", "Extension cannot contain both extension and value[x]", resource_type, resource_id, path, source="extension"))
        if modifier_context and extension_definition is not None and not extension_definition.is_modifier:
            issues.append(ValidationIssue(Severity.ERROR, "modifierExtension.definition", "modifierExtension must reference an extension definition marked as modifier", resource_type, resource_id, path, source="extension"))
        if extension_definition is not None:
            if len(value_fields) < extension_definition.min_value:
                issues.append(ValidationIssue(Severity.ERROR, "extension.value.min", f"Extension {extension_url} requires value[x]", resource_type, resource_id, path, source="extension"))
            if extension_definition.max_value != "*" and len(value_fields) > int(extension_definition.max_value):
                issues.append(ValidationIssue(Severity.ERROR, "extension.value.max", f"Extension {extension_url} allows at most {extension_definition.max_value} value[x]", resource_type, resource_id, path, source="extension"))
            if value_fields and extension_definition.value_types:
                actual_type = VALUE_FIELD_TYPES.get(value_fields[0], value_fields[0].replace("value", "", 1))
                if actual_type not in extension_definition.value_types:
                    issues.append(ValidationIssue(Severity.ERROR, "extension.value.type", f"Extension {extension_url} value type {actual_type} is not allowed; expected {', '.join(extension_definition.value_types)}", resource_type, resource_id, f"{path}.{value_fields[0]}", source="extension"))
            nested_required = extension_definition.nested_extensions or {}
            nested_values = value.get("extension", [])
            if nested_required and isinstance(nested_values, list):
                present_urls = {
                    nested.get("url")
                    for nested in nested_values
                    if isinstance(nested, dict)
                }
                for name, constraint in nested_required.items():
                    if constraint.fixed not in present_urls and constraint.min > 0:
                        issues.append(ValidationIssue(Severity.ERROR, "extension.nested.required", f"Extension {extension_url} requires nested extension {constraint.fixed}", resource_type, resource_id, f"{path}.extension:{name}", source="extension"))
        return issues

    def _validate_meta_profiles(self, resource: dict[str, Any], definition: Any, *, bundle_entry: bool = False) -> list[ValidationIssue]:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        issues: list[ValidationIssue] = []
        if not isinstance(resource_type, str):
            return issues
        meta = resource.get("meta", {})
        declared = meta.get("profile", []) if isinstance(meta, dict) else []
        if declared and not isinstance(declared, list):
            issues.append(ValidationIssue(Severity.ERROR, "meta.profile.type", "meta.profile must be an array", resource_type, resource_id, f"{resource_type}.meta.profile", source="profile"))
            declared = []
        enforced = self.config.profiles.get(resource_type, [])
        matched_enforced = [p for p in enforced if p in declared]
        if enforced and not matched_enforced and not bundle_entry:
            issues.append(ValidationIssue(Severity.ERROR, "profile.enforced.missing", f"Resource must declare at least one enforced profile for {resource_type}", resource_type, resource_id, f"{resource_type}.meta.profile", source="profile"))
        active_enforced = matched_enforced if matched_enforced else (enforced[:1] if not bundle_entry else [])
        for profile_url in dict.fromkeys([*declared, *active_enforced]):
            profile = self.profile_registry.get(profile_url)
            if profile is None:
                if profile_url in enforced:
                    severity = Severity.ERROR
                elif not self.config.error_on_unknown_profile:
                    continue
                else:
                    severity = Severity.WARNING
                issues.append(ValidationIssue(severity, "profile.unknown", f"Profile {profile_url} is not loaded", resource_type, resource_id, f"{resource_type}.meta.profile", profile_url, source="profile"))
                continue
            if profile.resource_type != resource_type:
                issues.append(ValidationIssue(Severity.ERROR, "profile.type", f"Profile {profile_url} applies to {profile.resource_type}, not {resource_type}", resource_type, resource_id, f"{resource_type}.meta.profile", profile_url, source="profile"))
            issues.extend(self._validate_profile_element_constraints(resource, profile_url, profile))
            issues.extend(self._validate_profile_slices(resource, profile_url, profile))
            for field in profile.required:
                if not _choice_field_present(resource, field):
                    issues.append(ValidationIssue(Severity.ERROR, "profile.required", f"Profile requires {resource_type}.{field}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
            for field, (min_value, max_value) in (profile.cardinality or {}).items():
                if "[x]" in field and _choice_field_present(resource, field):
                    continue
                value = resource.get(field)
                count = len(value) if isinstance(value, list) else 0 if value in (None, "", []) else 1
                if count < min_value:
                    issues.append(ValidationIssue(Severity.ERROR, "profile.cardinality.min", f"Profile requires at least {min_value} value(s) for {resource_type}.{field}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
                if max_value != "*" and count > int(max_value):
                    issues.append(ValidationIssue(Severity.ERROR, "profile.cardinality.max", f"Profile allows at most {max_value} value(s) for {resource_type}.{field}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
            for field, expected in (profile.fixed or {}).items():
                if resource.get(field) != expected:
                    issues.append(ValidationIssue(Severity.ERROR, "profile.fixed", f"Profile fixed value mismatch for {resource_type}.{field}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
            for field, expected in (profile.patterns or {}).items():
                actual = resource.get(field)
                if isinstance(expected, dict) and isinstance(actual, dict):
                    if any(actual.get(k) != v for k, v in expected.items()):
                        issues.append(ValidationIssue(Severity.ERROR, "profile.pattern", f"Profile pattern mismatch for {resource_type}.{field}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
            for field, (strength, value_set) in (profile.bindings or {}).items():
                value = resource.get(field)
                vs_key = value_set.split("|")[0].rsplit("/", 1)[-1]
                codes = [value] if isinstance(value, str) else _codeable_concept_codes(value)
                for code in codes:
                    contains = self.terminology.contains(vs_key, code)
                    if contains is False and strength == "required":
                        issues.append(ValidationIssue(Severity.ERROR, "profile.binding.required", f"Code {code!r} is not in required profile ValueSet {value_set}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
                    elif contains is False and strength == "extensible":
                        issues.append(ValidationIssue(Severity.WARNING, "profile.binding.extensible", f"Code {code!r} is not in extensible profile ValueSet {value_set}", resource_type, resource_id, f"{resource_type}.{field}", profile_url, source="profile"))
            for key, invariant_severity, expression in profile.invariants:
                if key in _SKIP_INVARIANTS:
                    continue
                result = evaluate(resource, expression)
                if result is False:
                    issue_severity = Severity.ERROR if invariant_severity == "error" else Severity.WARNING
                    issues.append(ValidationIssue(issue_severity, f"profile.invariant.{key}", f"FHIRPath invariant failed: {expression}", resource_type, resource_id, resource_type, profile_url, source="fhirpath"))
                elif result is None:
                    issues.append(ValidationIssue(Severity.WARNING, f"profile.invariant.unsupported.{key}", f"FHIRPath invariant is not supported by the lightweight evaluator: {expression}", resource_type, resource_id, resource_type, profile_url, source="fhirpath"))
        return issues

    def _validate_profile_slices(self, resource: dict[str, Any], profile_url: str, profile: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        sliced_paths_matched: dict[str, set[int]] = {}
        for key, slice_constraint in (profile.slices or {}).items():
            if slice_constraint.path.endswith("[x]"):
                parent_values = self._choice_type_values(resource, slice_constraint.path)
            else:
                parent_values = values_at_path(resource, slice_constraint.path)
            matched_indices: list[int] = []
            for idx, value in enumerate(parent_values):
                if self._matches_slice(value, slice_constraint):
                    matched_indices.append(idx)
            matched = [parent_values[i] for i in matched_indices]
            sliced_paths_matched.setdefault(slice_constraint.path, set()).update(matched_indices)
            issue_path = f"{resource_type}.{slice_constraint.path}:{slice_constraint.name}"
            count = len(matched)
            if count < slice_constraint.min:
                issues.append(ValidationIssue(Severity.ERROR, "profile.slice.cardinality.min", f"Profile slice {key} requires at least {slice_constraint.min} matching item(s)", resource_type, resource_id, issue_path, profile_url, source="profile"))
            if slice_constraint.max != "*" and count > int(slice_constraint.max):
                issues.append(ValidationIssue(Severity.ERROR, "profile.slice.cardinality.max", f"Profile slice {key} allows at most {slice_constraint.max} matching item(s)", resource_type, resource_id, issue_path, profile_url, source="profile"))
            for child_path, child_constraint in (slice_constraint.elements or {}).items():
                for value in matched:
                    child_values = values_at_path(value, child_path)
                    child_issue_path = f"{issue_path}.{child_path}"
                    child_parts = child_path.split(".")
                    if len(child_parts) > 1 and not values_at_path(value, ".".join(child_parts[:-1])):
                        continue
                    if len(child_values) < child_constraint.min:
                        issues.append(ValidationIssue(Severity.ERROR, "profile.slice.element.cardinality.min", f"Profile slice element {child_issue_path} requires at least {child_constraint.min} value(s)", resource_type, resource_id, child_issue_path, profile_url, source="profile"))
                    if child_constraint.max != "*" and len(child_values) > int(child_constraint.max):
                        issues.append(ValidationIssue(Severity.ERROR, "profile.slice.element.cardinality.max", f"Profile slice element {child_issue_path} allows at most {child_constraint.max} value(s)", resource_type, resource_id, child_issue_path, profile_url, source="profile"))
                    if child_constraint.fixed is not None and child_values and any(child != child_constraint.fixed for child in child_values):
                        issues.append(ValidationIssue(Severity.ERROR, "profile.slice.element.fixed", f"Profile slice fixed value mismatch for {child_issue_path}", resource_type, resource_id, child_issue_path, profile_url, source="profile"))
                    if child_constraint.pattern is not None and child_values and any(not _matches_pattern(child, child_constraint.pattern) for child in child_values):
                        issues.append(ValidationIssue(Severity.ERROR, "profile.slice.element.pattern", f"Profile slice pattern mismatch for {child_issue_path}", resource_type, resource_id, child_issue_path, profile_url, source="profile"))
                    if child_constraint.binding is not None:
                        strength, value_set = child_constraint.binding
                        for child in child_values:
                            if isinstance(child, str):
                                contains = self.terminology.contains(value_set.rsplit("/", 1)[-1], child)
                                if contains is False and strength == "required":
                                    issues.append(ValidationIssue(Severity.ERROR, "profile.slice.element.binding.required", f"Code {child!r} is not in required ValueSet {value_set}", resource_type, resource_id, f"{issue_path}.{child_path}", profile_url, source="profile"))
                                elif contains is False and strength == "extensible":
                                    issues.append(ValidationIssue(Severity.WARNING, "profile.slice.element.binding.extensible", f"Code {child!r} is not in extensible ValueSet {value_set}", resource_type, resource_id, f"{issue_path}.{child_path}", profile_url, source="profile"))
        slicing_rules = profile.slicing_rules or {}
        for sliced_path, rules in slicing_rules.items():
            if rules != "closed":
                continue
            if sliced_path.endswith("[x]"):
                parent_values = self._choice_type_values(resource, sliced_path)
            else:
                parent_values = values_at_path(resource, sliced_path)
            matched_indices_for_path = sliced_paths_matched.get(sliced_path, set())
            for idx, value in enumerate(parent_values):
                if idx not in matched_indices_for_path:
                    issues.append(ValidationIssue(Severity.ERROR, "profile.slice.closed", f"Element at {resource_type}.{sliced_path}[{idx}] does not match any defined slice", resource_type, resource_id, f"{resource_type}.{sliced_path}[{idx}]", profile_url, source="profile"))
        return issues

    def _choice_type_values(self, resource: dict[str, Any], choice_path: str) -> list[dict[str, Any]]:
        prefix = choice_path.split(".")[-1].replace("[x]", "")
        parts = choice_path.split(".")[:-1]
        containers = [resource]
        for part in parts:
            next_containers: list[Any] = []
            for container in containers:
                if isinstance(container, dict):
                    child = container.get(part)
                    if isinstance(child, list):
                        next_containers.extend(child)
                    elif child is not None:
                        next_containers.append(child)
            containers = next_containers
        results: list[dict[str, Any]] = []
        for container in containers:
            if not isinstance(container, dict):
                continue
            for field_name, field_value in container.items():
                if field_name.startswith(prefix) and field_name != prefix and field_name[len(prefix):][0:1].isupper():
                    suffix = field_name[len(prefix):]
                    fhir_type = VALUE_FIELD_TYPES.get(field_name) or _CHOICE_SUFFIX_TO_FHIR_TYPE.get(suffix, suffix)
                    wrapper = {"_choiceValue": field_value, "_choiceType": fhir_type}
                    results.append(wrapper)
        return results

    def _matches_slice(self, value: Any, slice_constraint: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if slice_constraint.discriminators:
            return all(self._matches_discriminator(value, discriminator, slice_constraint) for discriminator in slice_constraint.discriminators)
        constrained_elements = slice_constraint.elements or {}
        discriminator_like = [constraint for constraint in constrained_elements.values() if constraint.fixed is not None or constraint.pattern is not None]
        return bool(discriminator_like) and all(self._matches_element_constraint(value, constraint.path, constraint) for constraint in discriminator_like)

    def _matches_discriminator(self, value: dict[str, Any], discriminator: tuple[str, str], slice_constraint: Any) -> bool:
        kind, path = discriminator
        if kind == "exists":
            return bool(values_at_path(value, path))
        if kind == "type":
            return self._matches_type_discriminator(value, path, slice_constraint)
        if kind in ("pattern", "value") and path == "$this":
            if slice_constraint.pattern is not None:
                return _matches_pattern(value, slice_constraint.pattern)
            if slice_constraint.fixed is not None:
                return bool(value == slice_constraint.fixed)
        constraint = (slice_constraint.elements or {}).get(path)
        if constraint is not None:
            return self._matches_element_constraint(value, path, constraint)
        for element_path, element_constraint in (slice_constraint.elements or {}).items():
            if element_path.startswith(path + ".") or element_path == path:
                if element_constraint.fixed is not None or element_constraint.pattern is not None:
                    return self._matches_element_constraint(value, element_path, element_constraint)
        return bool(values_at_path(value, path))

    def _matches_type_discriminator(self, value: dict[str, Any], path: str, slice_constraint: Any) -> bool:
        if not slice_constraint.type_code:
            return False
        if path == "$this":
            if "_choiceType" in value:
                return bool(value["_choiceType"] == slice_constraint.type_code)
            if "resourceType" in value:
                return bool(value["resourceType"] == slice_constraint.type_code)
            for suffix, fhir_type in VALUE_FIELD_TYPES.items():
                if suffix in value:
                    return bool(fhir_type == slice_constraint.type_code)
            return False
        target_values = values_at_path(value, path)
        if not target_values:
            return False
        for target in target_values:
            if isinstance(target, dict):
                if "_choiceType" in target:
                    return bool(target["_choiceType"] == slice_constraint.type_code)
                if "resourceType" in target:
                    return bool(target["resourceType"] == slice_constraint.type_code)
                for suffix, fhir_type in VALUE_FIELD_TYPES.items():
                    if suffix in target:
                        return bool(fhir_type == slice_constraint.type_code)
        return False

    def _matches_element_constraint(self, value: dict[str, Any], path: str, constraint: Any) -> bool:
        values = values_at_path(value, path)
        if constraint.fixed is not None:
            return any(candidate == constraint.fixed for candidate in values)
        if constraint.pattern is not None:
            return any(_matches_pattern(candidate, constraint.pattern) for candidate in values)
        return bool(values)

    def _validate_profile_element_constraints(self, resource: dict[str, Any], profile_url: str, profile: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        for path, constraint in (profile.elements or {}).items():
            issue_path = f"{resource_type}.{path}"
            if "." in path:
                issues.extend(self._validate_nested_element_constraint(resource, resource_type, resource_id, path, constraint, issue_path, profile_url))
            else:
                issues.extend(self._validate_flat_element_constraint(resource, resource_type, resource_id, path, constraint, issue_path, profile_url))
        return issues

    def _validate_flat_element_constraint(self, resource: dict[str, Any], resource_type: str | None, resource_id: str | None, path: str, constraint: Any, issue_path: str, profile_url: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        values = values_at_path(resource, path)
        count = len(values)
        if count < constraint.min:
            issues.append(ValidationIssue(Severity.ERROR, "profile.element.cardinality.min", f"Profile requires at least {constraint.min} value(s) for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
        if constraint.max != "*" and count > int(constraint.max):
            issues.append(ValidationIssue(Severity.ERROR, "profile.element.cardinality.max", f"Profile allows at most {constraint.max} value(s) for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
        if constraint.fixed is not None:
            for value in values or [None]:
                if value != constraint.fixed:
                    issues.append(ValidationIssue(Severity.ERROR, "profile.element.fixed", f"Profile fixed value mismatch for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
                    break
        if constraint.pattern is not None:
            for value in values or [None]:
                if not _matches_pattern(value, constraint.pattern):
                    issues.append(ValidationIssue(Severity.ERROR, "profile.element.pattern", f"Profile pattern mismatch for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
                    break
        issues.extend(self._check_element_binding(values, constraint, resource_type, resource_id, issue_path, profile_url))
        issues.extend(self._check_element_invariants(values, constraint, resource_type, resource_id, issue_path, profile_url))
        return issues

    def _validate_nested_element_constraint(self, resource: dict[str, Any], resource_type: str | None, resource_id: str | None, path: str, constraint: Any, issue_path: str, profile_url: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        parts = path.split(".")
        parent_path = parts[0]
        child_path = ".".join(parts[1:])
        parents = values_at_path(resource, parent_path)
        if not parents:
            return issues
        all_child_values: list[Any] = []
        child_parts = child_path.split(".")
        for parent in parents:
            if not isinstance(parent, dict):
                continue
            if len(child_parts) > 1:
                intermediate_path = ".".join(child_parts[:-1])
                intermediate_parents = values_at_path(parent, intermediate_path)
                if not intermediate_parents:
                    continue
            child_values = values_at_path(parent, child_path)
            all_child_values.extend(child_values)
            count = len(child_values)
            if count < constraint.min:
                issues.append(ValidationIssue(Severity.ERROR, "profile.element.cardinality.min", f"Profile requires at least {constraint.min} value(s) for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
            if constraint.max != "*" and count > int(constraint.max):
                issues.append(ValidationIssue(Severity.ERROR, "profile.element.cardinality.max", f"Profile allows at most {constraint.max} value(s) for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
            if child_values:
                if constraint.fixed is not None and not any(v == constraint.fixed for v in child_values):
                    issues.append(ValidationIssue(Severity.ERROR, "profile.element.fixed", f"Profile fixed value mismatch for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
                if constraint.pattern is not None and not any(_matches_pattern(v, constraint.pattern) for v in child_values):
                    issues.append(ValidationIssue(Severity.ERROR, "profile.element.pattern", f"Profile pattern mismatch for {issue_path}", resource_type, resource_id, issue_path, profile_url, source="profile"))
            issues.extend(self._check_element_binding(child_values, constraint, resource_type, resource_id, issue_path, profile_url))
        if parents:
            issues.extend(self._check_element_invariants(all_child_values, constraint, resource_type, resource_id, issue_path, profile_url))
        return issues

    def _check_element_binding(self, values: list[Any], constraint: Any, resource_type: str | None, resource_id: str | None, issue_path: str, profile_url: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if constraint.binding is None:
            return issues
        strength, value_set = constraint.binding
        vs_key = value_set.split("|")[0].rsplit("/", 1)[-1]
        for value in values:
            codes = [value] if isinstance(value, str) else _codeable_concept_codes(value)
            for code in codes:
                contains = self.terminology.contains(vs_key, code)
                if contains is False and strength == "required":
                    issues.append(ValidationIssue(Severity.ERROR, "profile.element.binding.required", f"Code {code!r} is not in required profile ValueSet {value_set}", resource_type, resource_id, issue_path, profile_url, source="profile"))
                elif contains is False and strength == "extensible":
                    issues.append(ValidationIssue(Severity.WARNING, "profile.element.binding.extensible", f"Code {code!r} is not in extensible profile ValueSet {value_set}", resource_type, resource_id, issue_path, profile_url, source="profile"))
        return issues

    def _check_element_invariants(self, element_values: list[Any], constraint: Any, resource_type: str | None, resource_id: str | None, issue_path: str, profile_url: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for key, severity, expression in constraint.invariants:
            if key in _SKIP_INVARIANTS:
                continue
            contexts = [v for v in element_values if isinstance(v, dict)] or element_values
            if not contexts:
                continue
            for context in contexts:
                result = evaluate(context, expression) if isinstance(context, dict) else None
                if result is False:
                    issue_severity = Severity.ERROR if severity == "error" else Severity.WARNING
                    issues.append(ValidationIssue(issue_severity, f"profile.element.invariant.{key}", f"FHIRPath invariant failed: {expression}", resource_type, resource_id, issue_path, profile_url, source="fhirpath"))
                    break
                if result is None:
                    issues.append(ValidationIssue(Severity.WARNING, f"profile.element.invariant.unsupported.{key}", f"FHIRPath invariant is not supported by the lightweight evaluator: {expression}", resource_type, resource_id, issue_path, profile_url, source="fhirpath"))
                    break
        return issues

    def _validate_contained(self, resource: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        contained = resource.get("contained", [])
        if contained and not isinstance(contained, list):
            return [ValidationIssue(Severity.ERROR, "contained.type", "contained must be an array", resource.get("resourceType"), resource.get("id"), f"{resource.get('resourceType')}.contained", source="structure")]
        for idx, child in enumerate(contained):
            if isinstance(child, dict):
                if not child.get("id"):
                    issues.append(ValidationIssue(Severity.ERROR, "contained.id", "contained resources must have id for local references", resource.get("resourceType"), resource.get("id"), f"{resource.get('resourceType')}.contained[{idx}].id", source="structure"))
                issues.extend(self._validate_one(child, {}))
            else:
                issues.append(ValidationIssue(Severity.ERROR, "contained.resource", "contained entries must be resources", resource.get("resourceType"), resource.get("id"), f"{resource.get('resourceType')}.contained[{idx}]", source="structure"))
        return issues

    def _report(
        self,
        issues: list[ValidationIssue],
        resources: list[dict[str, Any]],
        input_source: str,
        input_payloads: list[Any],
        input_hashes: dict[str, str],
    ) -> ValidationReport:
        issues = [self._apply_severity_policy(issue) for issue in issues]
        issues = sorted(issues, key=lambda issue: issue.fingerprint())
        errors = any(issue.severity is Severity.ERROR for issue in issues)
        warnings = any(issue.severity is Severity.WARNING for issue in issues)
        status = Status.FAIL if errors else Status.WARN if warnings else Status.PASS
        definition_source = {
            "mode": "package" if self.loaded_structure_definitions else "builtin",
            "loadedStructureDefinitions": self.loaded_structure_definitions,
            "mergedSnapshots": self.merged_snapshots,
            "resourceDefinitions": len(self.resource_definitions),
            "complexTypeDefinitions": len(self.complex_type_fields),
            "fhirPathBackend": backend_name(),
            "packages": [package.to_dict() for package in self.resolved_packages],
        }
        report = ValidationReport(
            run_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            validator_version=__version__,
            fhir_version=self.config.fhir_version,
            input_source=input_source,
            resource_count=len(resources),
            configured_profiles=self.config.profiles,
            configured_igs=self.config.enabled_igs,
            terminology=self.terminology.evidence(),
            deterministic_hash=stable_hash(input_payloads, self.config.to_dict()),
            definition_source=definition_source,
            config_snapshot=self.config.to_dict(),
            input_hashes=input_hashes,
            replay={
                "inputSource": input_source,
                "inputFiles": sorted(input_hashes),
                "validatorCommand": None,
            },
            issues=issues,
            resources=self._resource_summaries(resources, issues),
            status=status,
        )
        logger.debug(
            "validation report built",
            extra={
                "run_id": report.run_id,
                "input_source": input_source,
                "resource_count": report.resource_count,
                "issue_count": len(issues),
                "error_count": len(report.errors),
                "warning_count": len(report.warnings),
                "status": status.value,
            },
        )
        return report

    def _resource_summaries(self, resources: list[dict[str, Any]], issues: list[ValidationIssue]) -> list[ResourceValidationSummary]:
        return [
            self._resource_summary(index, resource, self._issues_for_resource(resource, issues))
            for index, resource in enumerate(resources)
        ]

    def _resource_summary(self, index: int, resource: dict[str, Any], issues: list[ValidationIssue]) -> ResourceValidationSummary:
        error_count = sum(1 for issue in issues if issue.severity is Severity.ERROR)
        warning_count = sum(1 for issue in issues if issue.severity is Severity.WARNING)
        information_count = sum(1 for issue in issues if issue.severity is Severity.INFORMATION)
        status = Status.FAIL if error_count else Status.WARN if warning_count else Status.PASS
        return ResourceValidationSummary(
            index=index,
            resource_type=resource.get("resourceType") if isinstance(resource.get("resourceType"), str) else None,
            resource_id=resource.get("id") if isinstance(resource.get("id"), str) else None,
            profiles=tuple(self._profiles_for_resource(resource)),
            status=status,
            issue_count=len(issues),
            error_count=error_count,
            warning_count=warning_count,
            information_count=information_count,
            issues=tuple(issues),
        )

    def _issues_for_resource(self, resource: dict[str, Any], issues: list[ValidationIssue]) -> list[ValidationIssue]:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        return [
            issue
            for issue in issues
            if issue.resource_type == resource_type and (resource_id is None or issue.resource_id in {None, resource_id})
        ]

    def _profiles_for_resource(self, resource: dict[str, Any]) -> list[str]:
        profiles: list[str] = []
        meta = resource.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("profile"), list):
            profiles.extend(profile for profile in meta["profile"] if isinstance(profile, str))
        resource_type = resource.get("resourceType")
        if isinstance(resource_type, str):
            profiles.extend(self.config.profiles.get(resource_type, []))
        return list(dict.fromkeys(profiles))

    def _apply_severity_policy(self, issue: ValidationIssue) -> ValidationIssue:
        configured = self.config.severity_policy.get(issue.code)
        if configured is None:
            configured = self.config.severity_policy.get(issue.source)
        if configured is None:
            return issue
        try:
            severity = Severity(configured)
        except ValueError:
            return issue
        return ValidationIssue(
            severity=severity,
            code=issue.code,
            message=issue.message,
            resource_type=issue.resource_type,
            resource_id=issue.resource_id,
            path=issue.path,
            profile=issue.profile,
            diagnostics=issue.diagnostics,
            source=issue.source,
        )
