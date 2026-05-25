from __future__ import annotations

from enum import Enum
from typing import Any


class SupportLevel(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partially_supported"
    UNSUPPORTED = "unsupported"


RULE_CLASSES: dict[str, dict[str, Any]] = {
    "resourceType": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate resourceType is present and refers to a known R4 type",
        "issueCodes": ["resourceType.required", "resourceType.unknown"],
    },
    "knownElements": {
        "support": SupportLevel.SUPPORTED,
        "description": "Reject elements not defined in the StructureDefinition",
        "issueCodes": ["element.unknown"],
    },
    "primitiveTypes": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate primitive type formats (boolean, integer, date, id, etc.)",
        "issueCodes": ["datatype.invalid"],
    },
    "objectArrayShape": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate that single-valued elements are not arrays and vice versa",
        "issueCodes": ["cardinality.max", "datatype.invalid"],
    },
    "cardinalityMin": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate minimum cardinality constraints from StructureDefinitions",
        "issueCodes": ["cardinality.min"],
    },
    "cardinalityMax": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate maximum cardinality constraints from StructureDefinitions",
        "issueCodes": ["cardinality.max"],
    },
    "requiredFields": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate that required fields (min >= 1) are present",
        "issueCodes": ["cardinality.min"],
    },
    "nestedElements": {
        "support": SupportLevel.SUPPORTED,
        "description": "Recursively validate nested BackboneElement children",
        "issueCodes": ["cardinality.min", "element.unknown", "datatype.invalid"],
    },
    "fixedValues": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate fixed[x] constraints from profiles",
        "issueCodes": ["profile.fixed", "profile.element.fixed"],
    },
    "patternValues": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate pattern[x] constraints from profiles",
        "issueCodes": ["profile.pattern", "profile.element.pattern"],
    },
    "extensionStructure": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate extension url, value[x] cardinality, nested extensions",
        "issueCodes": [
            "extension.url",
            "extension.value.multiple",
            "extension.nested-value",
            "extension.value.type",
            "extension.nested.required",
        ],
    },
    "extensionUrl": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate extension URL resolves to a loaded StructureDefinition",
        "issueCodes": ["extension.unknown"],
    },
    "codeableConceptShape": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate CodeableConcept and Coding structural shape",
        "issueCodes": ["datatype.invalid", "datatype.unknown-field"],
    },
    "requiredBinding": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate codes against required ValueSet bindings when locally available",
        "issueCodes": [
            "terminology.required",
            "terminology.code-system",
            "profile.binding.required",
            "profile.element.binding.required",
        ],
    },
    "profileMatch": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate meta.profile declarations and enforce configured profiles",
        "issueCodes": [
            "profile.enforced.missing",
            "profile.unknown",
            "profile.type",
            "profile.required",
            "profile.cardinality.min",
            "profile.cardinality.max",
        ],
    },
    "referenceFormat": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate Reference.reference format and structure",
        "issueCodes": ["reference.format"],
    },
    "referenceType": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate Reference target type against allowed targetProfile",
        "issueCodes": ["reference.type"],
    },
    "containedReferences": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate contained resource references resolve",
        "issueCodes": ["reference.contained.unresolved"],
    },
    "bundleStructure": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate Bundle type-specific entry constraints",
        "issueCodes": [
            "bundle.entry.resource",
            "bundle.document.composition",
            "bundle.message.header",
            "bundle.entry.request",
            "bundle.entry.response",
        ],
    },
    "choiceTypes": {
        "support": SupportLevel.SUPPORTED,
        "description": "Validate that at most one choice[x] variant is present",
        "issueCodes": ["choice.multiple"],
    },
    "slicing": {
        "support": SupportLevel.PARTIAL,
        "description": "Validate slice discriminators and per-slice cardinality",
        "limitations": "Value and exists discriminators supported; profile discriminators partial",
        "issueCodes": [
            "profile.slice.cardinality.min",
            "profile.slice.cardinality.max",
            "profile.slice.closed",
        ],
    },
    "fhirpathInvariants": {
        "support": SupportLevel.PARTIAL,
        "description": "Evaluate FHIRPath constraint expressions from profiles",
        "limitations": "Uses fhirpathpy; complex expressions may return unsupported",
        "issueCodes": ["profile.invariant.*"],
    },
    "extensibleBinding": {
        "support": SupportLevel.PARTIAL,
        "description": "Warn on codes outside extensible ValueSet bindings",
        "limitations": "Only checked when ValueSet is locally available",
        "issueCodes": ["terminology.extensible", "profile.binding.extensible"],
    },
    "terminologyServer": {
        "support": SupportLevel.UNSUPPORTED,
        "description": "Remote terminology server validation",
        "limitations": "No remote terminology server calls in first milestone",
        "issueCodes": [],
    },
    "crossResourceReferences": {
        "support": SupportLevel.UNSUPPORTED,
        "description": "Cross-resource reference resolution beyond local bundle/set",
        "limitations": "References outside the validation set are not fetched",
        "issueCodes": [],
    },
    "questionnaireValidation": {
        "support": SupportLevel.UNSUPPORTED,
        "description": "QuestionnaireResponse validation against paired Questionnaire",
        "limitations": "Not implemented in first milestone",
        "issueCodes": [],
    },
    "xmlValidation": {
        "support": SupportLevel.UNSUPPORTED,
        "description": "XML and RDF format validation",
        "limitations": "Only JSON is supported",
        "issueCodes": [],
    },
    "fullValueSetExpansion": {
        "support": SupportLevel.UNSUPPORTED,
        "description": "Full ValueSet expansion with include/exclude/filter semantics",
        "limitations": "Basic local expansion only; no hierarchical subsumption",
        "issueCodes": [],
    },
}


def rule_support_summary() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        SupportLevel.SUPPORTED.value: [],
        SupportLevel.PARTIAL.value: [],
        SupportLevel.UNSUPPORTED.value: [],
    }
    for name, info in RULE_CLASSES.items():
        result[info["support"].value].append(name)
    return result


def issue_code_to_rule_class(code: str) -> str | None:
    for name, info in RULE_CLASSES.items():
        for pattern in info["issueCodes"]:
            if pattern.endswith(".*"):
                if code.startswith(pattern[:-1]):
                    return name
            elif code == pattern:
                return name
    return None
