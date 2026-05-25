from __future__ import annotations

from typing import Any

from pyfhircheck.models import Severity, ValidationIssue


class CustomRuleRunner:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules

    def validate(self, resource: dict[str, Any], bundle_index: dict[str, dict[str, Any]]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        if resource_type == "Patient":
            required_system = self.rules.get("patientIdentifierSystem")
            if required_system:
                identifiers = resource.get("identifier", [])
                if not isinstance(identifiers, list) or not any(i.get("system") == required_system for i in identifiers if isinstance(i, dict)):
                    issues.append(ValidationIssue(Severity.ERROR, "custom.patient.identifier.system", f"Patient.identifier must contain system {required_system}", resource_type, resource_id, "Patient.identifier", source="custom-rules"))
        if resource_type == "Encounter" and self.rules.get("encounterRequiresPatient", True):
            subject = resource.get("subject", {})
            reference = subject.get("reference") if isinstance(subject, dict) else None
            if isinstance(reference, str):
                is_patient = reference.startswith("Patient/")
                if not is_patient and reference in bundle_index:
                    is_patient = bundle_index[reference].get("resourceType") == "Patient"
                if not is_patient:
                    issues.append(ValidationIssue(Severity.ERROR, "custom.encounter.patient", "Encounter.subject must reference a Patient", resource_type, resource_id, "Encounter.subject", source="custom-rules"))
            else:
                issues.append(ValidationIssue(Severity.ERROR, "custom.encounter.patient", "Encounter.subject must reference a Patient", resource_type, resource_id, "Encounter.subject", source="custom-rules"))
        if resource_type == "Composition":
            sections = self.rules.get("compositionRequiredSections", [])
            if sections:
                present = {s.get("title") for s in resource.get("section", []) if isinstance(s, dict)}
                for section in sections:
                    if section not in present:
                        issues.append(ValidationIssue(Severity.ERROR, "custom.composition.section", f"Composition must contain section {section}", resource_type, resource_id, "Composition.section", source="custom-rules"))
        if self.rules.get("resolveLocalReferences", False):
            issues.extend(_reference_issues(resource, bundle_index))
        return issues

    def validate_bundle(self, bundle: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        required_types = self.rules.get("bundleRequiredResourceTypes", [])
        if required_types and bundle.get("resourceType") == "Bundle":
            present = {
                entry.get("resource", {}).get("resourceType")
                for entry in bundle.get("entry", [])
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
            }
            for resource_type in required_types:
                if resource_type not in present:
                    issues.append(ValidationIssue(Severity.ERROR, "custom.bundle.required-type", f"Bundle must include resource type {resource_type}", "Bundle", bundle.get("id"), "Bundle.entry", source="custom-rules"))
        return issues


def _reference_issues(resource: dict[str, Any], bundle_index: dict[str, dict[str, Any]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    resource_type = resource.get("resourceType")
    resource_id = resource.get("id")

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            ref = value.get("reference")
            if isinstance(ref, str) and "/" in ref and not ref.startswith("http") and ref not in bundle_index:
                issues.append(ValidationIssue(Severity.ERROR, "custom.reference.resolve", f"Reference {ref} does not resolve locally", resource_type, resource_id, path, source="custom-rules"))
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(resource, resource_type or "")
    return issues
