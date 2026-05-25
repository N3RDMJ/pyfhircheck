from __future__ import annotations

from typing import Any


RULES: dict[str, dict[str, Any]] = {
    "json.invalid": {
        "category": "syntax",
        "repairability": "automatic",
        "skill": "json-repair",
        "hint": "Fix the JSON syntax at the reported file location before validating FHIR semantics.",
    },
    "json.not-object": {
        "category": "syntax",
        "repairability": "automatic",
        "skill": "json-repair",
        "hint": "FHIR resource files must contain one JSON object, not an array or scalar value.",
    },
    "resourceType.required": {
        "category": "structure",
        "repairability": "automatic",
        "skill": "fhir-structure",
        "hint": "Add a valid FHIR R4 resourceType property.",
    },
    "resourceType.unknown": {
        "category": "structure",
        "repairability": "manual",
        "skill": "fhir-structure",
        "hint": "Use a supported FHIR R4 resource type or load package definitions for this type.",
    },
    "element.unknown": {
        "category": "structure",
        "repairability": "automatic",
        "skill": "fhir-structure",
        "hint": "Remove the unknown element or move the data into a valid FHIR element or extension.",
    },
    "cardinality.min": {
        "category": "structure",
        "repairability": "contextual",
        "skill": "fhir-cardinality",
        "hint": "Add the required element with a domain-correct value.",
    },
    "datatype.invalid": {
        "category": "datatype",
        "repairability": "automatic",
        "skill": "fhir-datatype",
        "hint": "Replace the value with one matching the FHIR datatype format for the reported path.",
    },
    "terminology.required": {
        "category": "terminology",
        "repairability": "contextual",
        "skill": "terminology",
        "hint": "Use a code from the required ValueSet, or update local terminology/package configuration if the code is valid.",
    },
    "profile.unknown": {
        "category": "profile",
        "repairability": "configuration",
        "skill": "profile-loading",
        "hint": "Load the StructureDefinition package or disable unknown-profile errors if this profile is intentionally unavailable.",
    },
    "profile.enforced.missing": {
        "category": "profile",
        "repairability": "automatic",
        "skill": "profile-metadata",
        "hint": "Declare the enforced profile URL in meta.profile.",
    },
    "profile.required": {
        "category": "profile",
        "repairability": "contextual",
        "skill": "profile-cardinality",
        "hint": "Add the element required by the declared or enforced profile.",
    },
    "reference.unresolved": {
        "category": "reference",
        "repairability": "contextual",
        "skill": "reference-resolution",
        "hint": "Add the referenced resource to the validation set or correct the reference.",
    },
    "reference.type": {
        "category": "reference",
        "repairability": "automatic",
        "skill": "reference-resolution",
        "hint": "Change the reference target type to one allowed by the element definition.",
    },
}


PREFIX_RULES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("bundle.", {"category": "bundle", "repairability": "contextual", "skill": "bundle-structure", "hint": "Fix the Bundle shape at the reported entry/path."}),
    ("choice.", {"category": "choice", "repairability": "automatic", "skill": "fhir-choice-elements", "hint": "Use exactly one valid choice element for this FHIR [x] field."}),
    ("contained.", {"category": "contained", "repairability": "contextual", "skill": "contained-resources", "hint": "Fix contained resource structure or local contained references."}),
    ("custom.", {"category": "custom-rule", "repairability": "contextual", "skill": "project-rule", "hint": "Apply the configured project-specific validation rule."}),
    ("extension.", {"category": "extension", "repairability": "contextual", "skill": "extension-definition", "hint": "Align the extension URL, value[x], or nested extensions with the loaded definition."}),
    ("profile.", {"category": "profile", "repairability": "contextual", "skill": "profile-validation", "hint": "Align the resource with the loaded StructureDefinition constraints."}),
    ("reference.", {"category": "reference", "repairability": "contextual", "skill": "reference-resolution", "hint": "Correct the reference format, type, or target availability."}),
)


def explain_rule(code: str) -> dict[str, Any]:
    if code in RULES:
        return {"code": code, **RULES[code]}
    for prefix, metadata in PREFIX_RULES:
        if code.startswith(prefix):
            return {"code": code, **metadata}
    return {
        "code": code,
        "category": "unknown",
        "repairability": "manual",
        "skill": "validator-triage",
        "hint": "Inspect the issue path, message, and source to decide the next repair step.",
    }


def rule_catalog() -> list[dict[str, Any]]:
    return [explain_rule(code) for code in sorted(RULES)]
