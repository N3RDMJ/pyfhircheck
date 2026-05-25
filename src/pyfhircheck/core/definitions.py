from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class ResourceDef:
    required: tuple[str, ...] = ()
    elements: dict[str, ElementDef] = field(default_factory=dict)


COMMON_ELEMENTS = {
    "id": ElementDef(("id",)),
    "meta": ElementDef(("Meta",)),
    "implicitRules": ElementDef(("uri",)),
    "language": ElementDef(("code",)),
    "text": ElementDef(("Narrative",)),
    "contained": ElementDef(("Resource",), max="*"),
    "extension": ElementDef(("Extension",), max="*"),
    "modifierExtension": ElementDef(("Extension",), max="*", modifier=True),
}

R4_RESOURCES: dict[str, ResourceDef] = {
    "Patient": ResourceDef(
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "active": ElementDef(("boolean",)),
            "name": ElementDef(("HumanName",), max="*"),
            "telecom": ElementDef(("ContactPoint",), max="*"),
            "gender": ElementDef(("code",), required_binding="administrative-gender"),
            "birthDate": ElementDef(("date",)),
            "deceasedBoolean": ElementDef(("boolean",)),
            "deceasedDateTime": ElementDef(("dateTime",)),
            "address": ElementDef(("Address",), max="*"),
            "managingOrganization": ElementDef(("Reference",), target_types=("Organization",)),
            "link": ElementDef(("BackboneElement",), max="*"),
        }
    ),
    "Practitioner": ResourceDef(
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "active": ElementDef(("boolean",)),
            "name": ElementDef(("HumanName",), max="*"),
            "telecom": ElementDef(("ContactPoint",), max="*"),
            "address": ElementDef(("Address",), max="*"),
            "gender": ElementDef(("code",), required_binding="administrative-gender"),
            "birthDate": ElementDef(("date",)),
        }
    ),
    "Encounter": ResourceDef(
        required=("status", "class",),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "status": ElementDef(("code",), min=1, required_binding="encounter-status"),
            "class": ElementDef(("Coding",), min=1),
            "type": ElementDef(("CodeableConcept",), max="*"),
            "subject": ElementDef(("Reference",), target_types=("Patient",)),
            "period": ElementDef(("Period",)),
        },
    ),
    "Composition": ResourceDef(
        required=("status", "type", "subject", "date", "author", "title"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",)),
            "status": ElementDef(("code",), min=1),
            "type": ElementDef(("CodeableConcept",), min=1),
            "subject": ElementDef(("Reference",), min=1),
            "date": ElementDef(("dateTime",), min=1),
            "author": ElementDef(("Reference",), min=1, max="*"),
            "title": ElementDef(("string",), min=1),
            "section": ElementDef(("BackboneElement",), max="*"),
        },
    ),
    "Observation": ResourceDef(
        required=("status", "code"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "status": ElementDef(("code",), min=1, required_binding="observation-status"),
            "category": ElementDef(("CodeableConcept",), max="*"),
            "code": ElementDef(("CodeableConcept",), min=1),
            "subject": ElementDef(("Reference",), target_types=("Patient",)),
            "effectiveDateTime": ElementDef(("dateTime",)),
            "effectivePeriod": ElementDef(("Period",)),
            "valueString": ElementDef(("string",)),
            "valueBoolean": ElementDef(("boolean",)),
            "valueCodeableConcept": ElementDef(("CodeableConcept",)),
            "valueQuantity": ElementDef(("Quantity",)),
            "dataAbsentReason": ElementDef(("CodeableConcept",)),
            "component": ElementDef(("BackboneElement",), max="*"),
        },
    ),
    "Condition": ResourceDef(
        required=("code", "subject"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "clinicalStatus": ElementDef(("CodeableConcept",)),
            "verificationStatus": ElementDef(("CodeableConcept",)),
            "category": ElementDef(("CodeableConcept",), max="*"),
            "severity": ElementDef(("CodeableConcept",)),
            "code": ElementDef(("CodeableConcept",), min=1),
            "subject": ElementDef(("Reference",), min=1, target_types=("Patient",)),
            "encounter": ElementDef(("Reference",), target_types=("Encounter",)),
            "onsetDateTime": ElementDef(("dateTime",)),
            "abatementDateTime": ElementDef(("dateTime",)),
            "recordedDate": ElementDef(("dateTime",)),
        },
    ),
    "Procedure": ResourceDef(
        required=("status", "subject"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "status": ElementDef(("code",), min=1, required_binding="event-status"),
            "code": ElementDef(("CodeableConcept",)),
            "subject": ElementDef(("Reference",), min=1, target_types=("Patient",)),
            "encounter": ElementDef(("Reference",), target_types=("Encounter",)),
            "performedDateTime": ElementDef(("dateTime",)),
            "performer": ElementDef(("BackboneElement",), max="*"),
        },
    ),
    "MedicationRequest": ResourceDef(
        required=("status", "intent", "medicationCodeableConcept", "subject"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "status": ElementDef(("code",), min=1, required_binding="medicationrequest-status"),
            "intent": ElementDef(("code",), min=1, required_binding="medicationrequest-intent"),
            "medicationCodeableConcept": ElementDef(("CodeableConcept",), min=1, choices=("medicationCodeableConcept", "medicationReference")),
            "medicationReference": ElementDef(("Reference",), choices=("medicationCodeableConcept", "medicationReference")),
            "subject": ElementDef(("Reference",), min=1, target_types=("Patient",)),
            "encounter": ElementDef(("Reference",), target_types=("Encounter",)),
            "authoredOn": ElementDef(("dateTime",)),
            "requester": ElementDef(("Reference",), target_types=("Practitioner", "Organization", "Patient")),
        },
    ),
    "DiagnosticReport": ResourceDef(
        required=("status", "code"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "status": ElementDef(("code",), min=1, required_binding="diagnostic-report-status"),
            "category": ElementDef(("CodeableConcept",), max="*"),
            "code": ElementDef(("CodeableConcept",), min=1),
            "subject": ElementDef(("Reference",), target_types=("Patient",)),
            "encounter": ElementDef(("Reference",), target_types=("Encounter",)),
            "effectiveDateTime": ElementDef(("dateTime",)),
            "result": ElementDef(("Reference",), max="*", target_types=("Observation",)),
            "presentedForm": ElementDef(("Attachment",), max="*"),
        },
    ),
    "DocumentReference": ResourceDef(
        required=("status", "content"),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",), max="*"),
            "status": ElementDef(("code",), min=1, required_binding="document-reference-status"),
            "type": ElementDef(("CodeableConcept",)),
            "subject": ElementDef(("Reference",), target_types=("Patient",)),
            "date": ElementDef(("instant",)),
            "author": ElementDef(("Reference",), max="*"),
            "content": ElementDef(("BackboneElement",), min=1, max="*"),
        },
    ),
    "MessageHeader": ResourceDef(
        required=("eventCoding", "source"),
        elements={
            **COMMON_ELEMENTS,
            "eventCoding": ElementDef(("Coding",), min=1, choices=("eventCoding", "eventUri")),
            "eventUri": ElementDef(("uri",), choices=("eventCoding", "eventUri")),
            "destination": ElementDef(("BackboneElement",), max="*"),
            "source": ElementDef(("BackboneElement",), min=1),
            "focus": ElementDef(("Reference",), max="*"),
            "response": ElementDef(("BackboneElement",)),
        },
    ),
    "Organization": ResourceDef(
        elements={**COMMON_ELEMENTS, "identifier": ElementDef(("Identifier",), max="*"), "name": ElementDef(("string",))}
    ),
    "Bundle": ResourceDef(
        required=("type",),
        elements={
            **COMMON_ELEMENTS,
            "identifier": ElementDef(("Identifier",)),
            "type": ElementDef(("code",), min=1, required_binding="bundle-type"),
            "timestamp": ElementDef(("instant",)),
            "total": ElementDef(("integer",)),
            "link": ElementDef(("BackboneElement",), max="*"),
            "entry": ElementDef(("BackboneElement",), max="*"),
        },
    ),
}

VALUE_SETS = {
    "administrative-gender": {"male", "female", "other", "unknown"},
    "encounter-status": {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled", "entered-in-error", "unknown"},
    "observation-status": {"registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"},
    "bundle-type": {"document", "message", "transaction", "transaction-response", "batch", "batch-response", "history", "searchset", "collection"},
    "event-status": {"preparation", "in-progress", "not-done", "on-hold", "stopped", "completed", "entered-in-error", "unknown"},
    "medicationrequest-status": {"active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"},
    "medicationrequest-intent": {"proposal", "plan", "order", "original-order", "reflex-order", "filler-order", "instance-order", "option"},
    "diagnostic-report-status": {"registered", "partial", "preliminary", "final", "amended", "corrected", "appended", "cancelled", "entered-in-error", "unknown"},
    "document-reference-status": {"current", "superseded", "entered-in-error"},
}

COMPLEX_TYPE_FIELDS: dict[str, dict[str, tuple[type, ...]]] = {
    "Identifier": {"system": (str,), "value": (str,), "use": (str,)},
    "HumanName": {"family": (str,), "given": (list,), "prefix": (list,), "suffix": (list,), "text": (str,), "use": (str,)},
    "ContactPoint": {"system": (str,), "value": (str,), "use": (str,), "rank": (int,)},
    "Address": {"line": (list,), "city": (str,), "state": (str,), "postalCode": (str,), "country": (str,), "use": (str,)},
    "Coding": {"system": (str,), "code": (str,), "display": (str,), "version": (str,)},
    "CodeableConcept": {"coding": (list,), "text": (str,)},
    "Quantity": {"value": (int, float), "unit": (str,), "system": (str,), "code": (str,)},
    "Period": {"start": (str,), "end": (str,)},
    "Meta": {"profile": (list,), "versionId": (str,), "lastUpdated": (str,), "source": (str,), "tag": (list,), "security": (list,)},
    "Extension": {"url": (str,)},
    "Attachment": {"contentType": (str,), "url": (str,), "title": (str,), "data": (str,)},
}
