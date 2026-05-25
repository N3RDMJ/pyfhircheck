from __future__ import annotations

import json
import tarfile
from pathlib import Path

from pyfhircheck.config import PackageConfig, ValidatorConfig
from pyfhircheck.core.engine import Validator
from pyfhircheck.evidence.drift import compare_reports
from pyfhircheck.evidence.store import EvidenceStore
from pyfhircheck.models import Status
from pyfhircheck.profiles.package import PackageResolver
from pyfhircheck.reporting.output import operation_outcome
from pyfhircheck.terminology.resolver import TerminologyResolver


def valid_patient() -> dict:
    return {
        "resourceType": "Patient",
        "id": "pat-1",
        "meta": {"profile": ["http://example.org/fhir/StructureDefinition/patient-with-identifier"]},
        "identifier": [{"system": "https://hospital.example/mrn", "value": "123"}],
        "gender": "female",
        "birthDate": "1970-01-01",
    }


def test_valid_patient_passes() -> None:
    report = Validator().validate_resource(valid_patient())
    assert report.status is Status.PASS
    assert report.resource_count == 1
    assert report.to_dict()["counts"]["errors"] == 0


def test_invalid_patient_fails() -> None:
    report = Validator().validate_resource({"resourceType": "Patient", "id": "bad id", "gender": "wat", "unknown": True})
    assert report.status is Status.FAIL
    assert {issue.code for issue in report.issues} >= {"datatype.invalid", "terminology.required", "element.unknown"}


def test_bundle_validation() -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"fullUrl": "urn:uuid:pat-1", "resource": valid_patient()}],
    }
    report = Validator().validate_resource(bundle)
    assert report.status is Status.PASS


def test_bundle_fullurl_reference_resolution() -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": "urn:uuid:patient-1",
                "resource": {"resourceType": "Patient", "id": "pat-1", "gender": "female"},
            },
            {
                "resource": {
                    "resourceType": "Encounter",
                    "id": "enc-1",
                    "status": "finished",
                    "class": {"code": "AMB"},
                    "subject": {"reference": "urn:uuid:patient-1"},
                }
            },
        ],
    }
    report = Validator().validate_resource(bundle)
    assert not any(issue.code == "reference.unresolved" for issue in report.issues)


def test_absolute_reference_resolves_by_local_resource_key() -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "pat-1", "gender": "female"}},
            {
                "resource": {
                    "resourceType": "Encounter",
                    "id": "enc-1",
                    "status": "finished",
                    "class": {"code": "AMB"},
                    "subject": {"reference": "https://example.test/fhir/Patient/pat-1/_history/3"},
                }
            },
        ],
    }
    report = Validator().validate_resource(bundle)
    assert not any(issue.code in {"reference.external", "reference.unresolved"} for issue in report.issues)


def test_reference_type_and_contained_resolution() -> None:
    wrong_type = {
        "resourceType": "Encounter",
        "id": "enc-1",
        "status": "finished",
        "class": {"code": "AMB"},
        "subject": {"reference": "Organization/org-1"},
    }
    assert any(issue.code == "reference.type" for issue in Validator().validate_resource(wrong_type).issues)

    contained_ok = {
        "resourceType": "Encounter",
        "id": "enc-2",
        "contained": [{"resourceType": "Patient", "id": "p1", "gender": "female"}],
        "status": "finished",
        "class": {"code": "AMB"},
        "subject": {"reference": "#p1"},
    }
    assert not any(issue.code == "reference.contained.unresolved" for issue in Validator().validate_resource(contained_ok).issues)

    contained_missing = contained_ok | {"subject": {"reference": "#missing"}}
    assert any(issue.code == "reference.contained.unresolved" for issue in Validator().validate_resource(contained_missing).issues)


def test_reference_type_field_and_conditional_reference() -> None:
    wrong_reference_type_field = {
        "resourceType": "Encounter",
        "id": "enc-1",
        "status": "finished",
        "class": {"code": "AMB"},
        "subject": {"reference": "Patient/pat-1", "type": "Organization"},
    }
    assert any(issue.code == "reference.type" for issue in Validator().validate_resource(wrong_reference_type_field).issues)

    conditional_reference = {
        "resourceType": "Encounter",
        "id": "enc-2",
        "status": "finished",
        "class": {"code": "AMB"},
        "subject": {"reference": "Patient?identifier=https://hospital.example/mrn|123"},
    }
    report = Validator().validate_resource(conditional_reference)
    assert not any(issue.code == "reference.format" for issue in report.issues)


def test_bundle_file_expands_entries(tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": valid_patient()}]}), encoding="utf-8")
    report = Validator().validate_path(path)
    assert report.resource_count == 2
    assert report.status is Status.PASS


def test_profile_enforcement_through_config() -> None:
    config = ValidatorConfig(profiles={"Patient": ["http://example.org/fhir/StructureDefinition/patient-with-identifier"]})
    report = Validator(config).validate_resource({"resourceType": "Patient", "id": "pat-1"})
    assert report.status is Status.FAIL
    assert "profile.enforced.missing" in {issue.code for issue in report.issues}

    report_declared = Validator(config).validate_resource(
        {"resourceType": "Patient", "id": "pat-1", "meta": {"profile": ["http://example.org/fhir/StructureDefinition/patient-with-identifier"]}}
    )
    assert "profile.required" in {issue.code for issue in report_declared.issues}


def test_terminology_failure_behavior() -> None:
    report = Validator().validate_resource({"resourceType": "Patient", "id": "pat-1", "gender": "nonsense"})
    assert report.status is Status.FAIL
    assert any(issue.code == "terminology.required" for issue in report.issues)


def test_custom_rules() -> None:
    config = ValidatorConfig(custom_rules={"patientIdentifierSystem": "https://required.example"})
    report = Validator(config).validate_resource(valid_patient())
    assert report.status is Status.FAIL
    assert any(issue.code == "custom.patient.identifier.system" for issue in report.issues)


def test_json_report_format_and_evidence(tmp_path: Path) -> None:
    report = Validator().validate_resource(valid_patient())
    data = report.to_dict()
    assert data["runId"]
    assert data["deterministicHash"]
    assert data["status"] == "PASS"
    assert data["summary"]["totalResources"] == 1
    assert data["summary"]["resourcesByType"] == {"Patient": 1}
    assert data["summary"]["resourcesByProfile"] == {"http://example.org/fhir/StructureDefinition/patient-with-identifier": 1}
    assert data["summary"]["statusByResourceType"] == {"Patient": {"PASS": 1, "WARN": 0, "FAIL": 0}}
    assert data["resources"][0]["reference"] == "Patient/pat-1"
    assert data["resources"][0]["profiles"] == ["http://example.org/fhir/StructureDefinition/patient-with-identifier"]
    assert data["resources"][0]["status"] == "PASS"
    run_dir = EvidenceStore(tmp_path).write(report)
    assert (run_dir / "report.json").exists()
    assert (run_dir / "operation-outcome.json").exists()
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "config.json").exists()
    assert (run_dir / "inputs.json").exists()
    loaded = EvidenceStore.load_report(run_dir)
    assert loaded["runId"] == report.run_id


def test_report_groups_issues_by_resource_and_type() -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": valid_patient()},
            {"resource": {"resourceType": "Patient", "id": "bad id", "gender": "bad"}},
            {"resource": {"resourceType": "Observation", "id": "obs-1", "status": "bad"}},
        ],
    }

    data = Validator().validate_resource(bundle).to_dict()

    assert data["summary"]["totalResources"] == 4
    assert data["summary"]["resourcesByType"] == {"Bundle": 1, "Observation": 1, "Patient": 2}
    assert data["summary"]["statusByResourceType"]["Bundle"]["PASS"] == 1
    assert data["summary"]["statusByResourceType"]["Observation"]["FAIL"] == 1
    assert data["summary"]["statusByResourceType"]["Patient"]["FAIL"] == 1
    bad_patient = next(resource for resource in data["resources"] if resource["resourceId"] == "bad id")
    assert bad_patient["status"] == "FAIL"
    assert bad_patient["issueCount"] >= 1


def test_operation_outcome_includes_location_profile_and_precise_issue_type() -> None:
    config = ValidatorConfig(profiles={"Patient": ["http://example.test/StructureDefinition/patient"]})
    report = Validator(config).validate_resource({"resourceType": "Patient", "id": "bad id", "gender": "bad"})

    outcome = operation_outcome(report)
    id_issue = next(issue for issue in outcome["issue"] if issue["details"]["coding"][0]["code"] == "datatype.invalid")
    terminology_issue = next(issue for issue in outcome["issue"] if issue["details"]["coding"][0]["code"] == "terminology.required")

    assert id_issue["code"] == "invalid"
    assert id_issue["expression"] == ["Patient.id"]
    assert id_issue["location"] == ["/f:Patient/f:id"]
    assert terminology_issue["code"] == "code-invalid"
    profile_issue = next(issue for issue in outcome["issue"] if issue["details"]["coding"][0]["code"] == "profile.unknown")
    assert profile_issue["code"] == "business-rule"
    assert profile_issue["details"]["coding"][1] == {
        "system": "https://pyfhircheck.local/profiles",
        "code": "http://example.test/StructureDefinition/patient",
        "display": "Validated profile",
    }


def test_drift_comparison_detects_new_errors() -> None:
    before = Validator().validate_resource(valid_patient()).to_dict()
    after = Validator().validate_resource({"resourceType": "Patient", "id": "pat-1", "gender": "bad"}).to_dict()
    drift = compare_reports(before, after)
    assert drift["summary"]["newErrors"] >= 1
    assert drift["summary"]["status"] == "FAIL"


def test_local_structure_definition_constraints(tmp_path: Path) -> None:
    profile = tmp_path / "patient-profile.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/strict-patient",
                "type": "Patient",
                "snapshot": {
                    "element": [
                        {"path": "Patient.identifier", "min": 1, "max": "1"},
                        {"path": "Patient.active", "min": 1, "max": "1", "fixedBoolean": True},
                        {
                            "path": "Patient.gender",
                            "min": 1,
                            "binding": {
                                "strength": "required",
                                "valueSet": "http://hl7.org/fhir/ValueSet/administrative-gender",
                            },
                            "constraint": [{"key": "gender-present", "severity": "error", "expression": "gender.exists()"}],
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/strict-patient"]})
    patient = valid_patient() | {"active": False, "identifier": [{"value": "a"}, {"value": "b"}], "gender": "not-real"}
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/strict-patient"]}
    report = Validator(config).validate_resource(patient)
    codes = {issue.code for issue in report.issues}
    assert "profile.fixed" in codes
    assert "profile.cardinality.max" in codes
    assert "profile.binding.required" in codes or "terminology.required" in codes


def test_fhirpath_invariant_failure_from_profile(tmp_path: Path) -> None:
    profile = tmp_path / "patient-profile.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/needs-active",
                "type": "Patient",
                "snapshot": {
                    "element": [
                        {
                            "path": "Patient",
                            "constraint": [{"key": "active-present", "severity": "error", "expression": "active.exists()"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/needs-active"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/needs-active"]}
    report = Validator(config).validate_resource(patient)
    assert any(issue.code == "profile.invariant.active-present" for issue in report.issues)


def test_fhirpath_backend_handles_count_where_and_first(tmp_path: Path) -> None:
    profile = tmp_path / "patient-fhirpath-profile.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-fhirpath-profile",
                "type": "Patient",
                "snapshot": {
                    "element": [
                        {
                            "path": "Patient",
                            "constraint": [
                                {
                                    "key": "one-smith",
                                    "severity": "error",
                                    "expression": "name.where(family = 'Smith').count() = 1",
                                },
                                {
                                    "key": "first-given-ann",
                                    "severity": "error",
                                    "expression": "name.given.first() = 'Ann'",
                                },
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/patient-fhirpath-profile"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-fhirpath-profile"]}
    patient["name"] = [{"family": "Jones", "given": ["Bob"]}]
    report = Validator(config).validate_resource(patient)
    codes = {issue.code for issue in report.issues}
    assert report.to_dict()["definitionSource"]["fhirPathBackend"] == "fhirpathpy"
    assert "profile.invariant.one-smith" in codes
    assert "profile.invariant.first-given-ann" in codes


def test_extension_choice_and_bundle_document_rules() -> None:
    patient = valid_patient()
    patient["extension"] = [{"valueString": "a", "valueBoolean": True}]
    assert any(issue.code == "extension.url" for issue in Validator().validate_resource(patient).issues)

    med_request = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": "Aspirin"},
        "medicationReference": {"reference": "Medication/med-1"},
        "subject": {"reference": "Patient/pat-1"},
    }
    assert any(issue.code == "choice.multiple" for issue in Validator().validate_resource(med_request).issues)

    document_bundle = {"resourceType": "Bundle", "type": "document", "entry": [{"resource": valid_patient()}]}
    assert any(issue.code == "bundle.document.composition" for issue in Validator().validate_resource(document_bundle).issues)


def test_bundle_message_requires_message_header_first() -> None:
    bad = {"resourceType": "Bundle", "type": "message", "entry": [{"resource": valid_patient()}]}
    assert any(issue.code == "bundle.message.header" for issue in Validator().validate_resource(bad).issues)

    good = {
        "resourceType": "Bundle",
        "type": "message",
        "entry": [
            {
                "resource": {
                    "resourceType": "MessageHeader",
                    "id": "msg-1",
                    "eventCoding": {"code": "event"},
                    "source": {"name": "source"},
                }
            }
        ],
    }
    assert not any(issue.code == "bundle.message.header" for issue in Validator().validate_resource(good).issues)


def test_bundle_transaction_and_response_entry_rules() -> None:
    transaction = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"resource": valid_patient(), "request": {"method": "INVALID"}}],
    }
    transaction_codes = {issue.code for issue in Validator().validate_resource(transaction).issues}
    assert "bundle.entry.request.method" in transaction_codes
    assert "bundle.entry.request.url" in transaction_codes

    response = {"resourceType": "Bundle", "type": "transaction-response", "entry": [{"response": {}}]}
    assert any(issue.code == "bundle.entry.response.status" for issue in Validator().validate_resource(response).issues)


def test_bundle_searchset_and_history_rules() -> None:
    searchset = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": valid_patient(), "search": {"mode": "bad"}}],
    }
    search_codes = {issue.code for issue in Validator().validate_resource(searchset).issues}
    assert "bundle.searchset.total" in search_codes
    assert "bundle.entry.search.mode" in search_codes

    history = {
        "resourceType": "Bundle",
        "type": "history",
        "entry": [{"resource": valid_patient(), "request": {"method": "GET", "url": "Patient/p1"}}],
    }
    assert any(issue.code == "bundle.entry.response" for issue in Validator().validate_resource(history).issues)


def test_extension_definition_value_type_and_unknown_warning(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-favorite-color.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/favorite-color",
                "type": "Extension",
                "kind": "complex-type",
                "snapshot": {
                    "element": [
                        {"path": "Extension"},
                        {"path": "Extension.url", "fixedUri": "http://example.test/StructureDefinition/favorite-color"},
                        {"path": "Extension.value[x]", "min": 1, "max": "1", "type": [{"code": "string"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)])
    wrong_type = valid_patient()
    wrong_type["extension"] = [{"url": "http://example.test/StructureDefinition/favorite-color", "valueBoolean": True}]
    report = Validator(config).validate_resource(wrong_type)
    assert any(issue.code == "extension.value.type" for issue in report.issues)

    unknown = valid_patient()
    unknown["extension"] = [{"url": "http://example.test/StructureDefinition/unknown", "valueString": "x"}]
    assert any(issue.code == "extension.unknown" for issue in Validator(config).validate_resource(unknown).issues)


def test_modifier_extension_requires_modifier_definition(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-not-modifier.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/not-modifier",
                "type": "Extension",
                "kind": "complex-type",
                "snapshot": {
                    "element": [
                        {"path": "Extension", "isModifier": False},
                        {"path": "Extension.url", "fixedUri": "http://example.test/StructureDefinition/not-modifier"},
                        {"path": "Extension.value[x]", "min": 0, "max": "1", "type": [{"code": "boolean"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    patient = valid_patient()
    patient["modifierExtension"] = [{"url": "http://example.test/StructureDefinition/not-modifier", "valueBoolean": True}]
    report = Validator(ValidatorConfig(local_package_paths=[str(package_dir)])).validate_resource(patient)
    assert any(issue.code == "modifierExtension.definition" for issue in report.issues)


def test_extension_required_nested_extension(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-complex-extension.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/complex-extension",
                "type": "Extension",
                "kind": "complex-type",
                "snapshot": {
                    "element": [
                        {"path": "Extension"},
                        {"path": "Extension.url", "fixedUri": "http://example.test/StructureDefinition/complex-extension"},
                        {"path": "Extension.extension:part", "sliceName": "part", "min": 1, "max": "1"},
                        {"path": "Extension.extension:part.url", "min": 1, "max": "1", "fixedUri": "part"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    patient = valid_patient()
    patient["extension"] = [{"url": "http://example.test/StructureDefinition/complex-extension"}]
    report = Validator(ValidatorConfig(local_package_paths=[str(package_dir)])).validate_resource(patient)
    assert any(issue.code == "extension.nested.required" for issue in report.issues)


def test_severity_policy_can_demote_warning_or_error() -> None:
    config = ValidatorConfig(severity_policy={"terminology.required": "warning"})
    report = Validator(config).validate_resource({"resourceType": "Patient", "id": "pat-1", "gender": "bad"})
    assert report.status is Status.WARN
    assert all(issue.severity.value != "error" for issue in report.issues)


def test_package_structure_definitions_generate_resource_definitions(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-Foo.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "kind": "resource",
                "type": "Foo",
                "snapshot": {
                    "element": [
                        {"path": "Foo", "min": 0, "max": "*"},
                        {"path": "Foo.id", "min": 0, "max": "1", "type": [{"code": "id"}]},
                        {"path": "Foo.status", "min": 1, "max": "1", "type": [{"code": "code"}]},
                        {"path": "Foo.subject", "min": 0, "max": "1", "type": [{"code": "Reference", "targetProfile": ["http://hl7.org/fhir/StructureDefinition/Patient"]}]},
                        {"path": "Foo.value[x]", "min": 0, "max": "1", "type": [{"code": "string"}, {"code": "boolean"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)])
    validator = Validator(config)
    report = validator.validate_resource({"resourceType": "Foo", "id": "foo-1", "valueString": "x", "valueBoolean": True, "extra": 1})
    codes = {issue.code for issue in report.issues}
    assert report.to_dict()["definitionSource"]["mode"] == "package"
    assert "cardinality.min" in codes
    assert "choice.multiple" in codes
    assert "element.unknown" in codes


def test_tgz_package_loading_generates_complex_type_fields(tmp_path: Path) -> None:
    package_json = {
        "resourceType": "StructureDefinition",
        "kind": "complex-type",
        "type": "Money",
        "snapshot": {
            "element": [
                {"path": "Money", "min": 0, "max": "*"},
                {"path": "Money.value", "min": 0, "max": "1", "type": [{"code": "decimal"}]},
                {"path": "Money.currency", "min": 0, "max": "1", "type": [{"code": "code"}]},
            ]
        },
    }
    package_path = tmp_path / "mini-core.tgz"
    source = tmp_path / "StructureDefinition-Money.json"
    source.write_text(json.dumps(package_json), encoding="utf-8")
    with tarfile.open(package_path, "w:gz") as archive:
        archive.add(source, arcname="package/StructureDefinition-Money.json")
    validator = Validator(ValidatorConfig(local_package_paths=[str(package_path)]))
    assert validator.complex_type_fields["Money"]["value"] == (int, float)


def test_differential_profile_nested_element_constraints(tmp_path: Path) -> None:
    profile = tmp_path / "patient-name-profile.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-name-profile",
                "type": "Patient",
                "baseDefinition": "http://hl7.org/fhir/StructureDefinition/Patient",
                "differential": {
                    "element": [
                        {"path": "Patient.name", "min": 1, "max": "*"},
                        {"path": "Patient.name.family", "min": 1, "max": "1", "fixedString": "Smith"},
                        {"path": "Patient.identifier.system", "min": 1, "max": "1", "patternUri": "https://required.example/mrn"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/patient-name-profile"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-name-profile"]}
    patient["name"] = [{"family": "Jones"}]
    patient["identifier"] = [{"system": "https://wrong.example/mrn", "value": "123"}]
    report = Validator(config).validate_resource(patient)
    codes = {issue.code for issue in report.issues}
    assert "profile.element.fixed" in codes
    assert "profile.element.pattern" in codes


def test_differential_profile_nested_required_cardinality(tmp_path: Path) -> None:
    profile = tmp_path / "patient-name-profile.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-name-required",
                "type": "Patient",
                "differential": {"element": [{"path": "Patient.name.family", "min": 1, "max": "1"}]},
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/patient-name-required"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-name-required"]}
    patient["name"] = [{"given": ["NoFamily"]}]
    report = Validator(config).validate_resource(patient)
    assert any(issue.code == "profile.element.cardinality.min" and issue.path == "Patient.name.family" for issue in report.issues)


def test_package_valueset_binding_is_enforced(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "ValueSet-local-genders.json").write_text(
        json.dumps(
            {
                "resourceType": "ValueSet",
                "url": "http://example.test/ValueSet/local-genders",
                "compose": {
                    "include": [
                        {
                            "system": "http://example.test/CodeSystem/local-genders",
                            "concept": [{"code": "local-female"}, {"code": "local-male"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-local-patient.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/local-patient",
                "type": "Patient",
                "differential": {
                    "element": [
                        {
                            "path": "Patient.gender",
                            "min": 1,
                            "binding": {
                                "strength": "required",
                                "valueSet": "http://example.test/ValueSet/local-genders",
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)], profiles={"Patient": ["http://example.test/StructureDefinition/local-patient"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/local-patient"]}
    patient["gender"] = "female"
    report = Validator(config).validate_resource(patient)
    assert any(issue.code in {"profile.binding.required", "profile.element.binding.required"} for issue in report.issues)


def test_valueset_include_exclude_and_filter_expansion(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "CodeSystem-local-status.json").write_text(
        json.dumps(
            {
                "resourceType": "CodeSystem",
                "url": "http://example.test/CodeSystem/local-status",
                "concept": [
                    {"code": "alpha", "display": "Alpha", "property": [{"code": "category", "valueCode": "allowed"}]},
                    {"code": "beta", "display": "Beta", "property": [{"code": "category", "valueCode": "allowed"}]},
                    {"code": "gamma", "display": "Gamma", "property": [{"code": "category", "valueCode": "blocked"}]},
                ],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "ValueSet-filtered-status.json").write_text(
        json.dumps(
            {
                "resourceType": "ValueSet",
                "url": "http://example.test/ValueSet/filtered-status",
                "compose": {
                    "include": [
                        {
                            "system": "http://example.test/CodeSystem/local-status",
                            "filter": [{"property": "category", "op": "=", "value": "allowed"}],
                        }
                    ],
                    "exclude": [
                        {
                            "system": "http://example.test/CodeSystem/local-status",
                            "concept": [{"code": "beta"}],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-local-observation.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/local-observation",
                "type": "Observation",
                "differential": {
                    "element": [
                        {
                            "path": "Observation.status",
                            "min": 1,
                            "binding": {
                                "strength": "required",
                                "valueSet": "http://example.test/ValueSet/filtered-status",
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)], profiles={"Observation": ["http://example.test/StructureDefinition/local-observation"]})
    valid = {
        "resourceType": "Observation",
        "id": "obs-1",
        "meta": {"profile": ["http://example.test/StructureDefinition/local-observation"]},
        "status": "alpha",
        "code": {"text": "test"},
    }
    invalid = valid | {"status": "beta"}
    valid_report = Validator(config).validate_resource(valid)
    invalid_report = Validator(config).validate_resource(invalid)
    assert valid_report.to_dict()["terminology"]["loadedCodeSystems"] == 1
    assert valid_report.to_dict()["terminology"]["loadedValueSets"] == 1
    assert not any(issue.code in {"profile.binding.required", "profile.element.binding.required"} for issue in valid_report.issues)
    assert any(issue.code in {"profile.binding.required", "profile.element.binding.required"} for issue in invalid_report.issues)


def test_valueset_expansion_contains_nested_codes(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "ValueSet-expanded.json").write_text(
        json.dumps(
            {
                "resourceType": "ValueSet",
                "url": "http://example.test/ValueSet/expanded",
                "expansion": {
                    "contains": [
                        {"code": "outer", "contains": [{"code": "inner"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-expanded-observation.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/expanded-observation",
                "type": "Observation",
                "differential": {
                    "element": [
                        {
                            "path": "Observation.status",
                            "binding": {
                                "strength": "required",
                                "valueSet": "http://example.test/ValueSet/expanded",
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)], profiles={"Observation": ["http://example.test/StructureDefinition/expanded-observation"]})
    report = Validator(config).validate_resource(
        {
            "resourceType": "Observation",
            "id": "obs-1",
            "meta": {"profile": ["http://example.test/StructureDefinition/expanded-observation"]},
            "status": "inner",
            "code": {"text": "test"},
        }
    )
    assert not any(issue.code in {"profile.binding.required", "profile.element.binding.required"} for issue in report.issues)


def test_differential_profile_merges_base_snapshot_constraints(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-base-patient.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://hl7.org/fhir/StructureDefinition/Patient",
                "kind": "resource",
                "type": "Patient",
                "snapshot": {
                    "element": [
                        {"path": "Patient"},
                        {"path": "Patient.identifier", "min": 0, "max": "*"},
                        {"path": "Patient.identifier.system", "min": 1, "max": "1", "fixedUri": "https://base.example/mrn"},
                        {"path": "Patient.name", "min": 0, "max": "*"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-derived-patient.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/derived-patient",
                "type": "Patient",
                "baseDefinition": "http://hl7.org/fhir/StructureDefinition/Patient",
                "differential": {
                    "element": [
                        {"path": "Patient.name", "min": 1, "max": "*"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)], profiles={"Patient": ["http://example.test/StructureDefinition/derived-patient"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/derived-patient"]}
    patient["identifier"] = [{"system": "https://wrong.example/mrn", "value": "123"}]
    patient.pop("name", None)
    report = Validator(config).validate_resource(patient)
    codes = {issue.code for issue in report.issues}
    assert report.to_dict()["definitionSource"]["mergedSnapshots"] >= 1
    assert "profile.element.fixed" in codes
    assert "profile.cardinality.min" in codes


def test_differential_resource_definition_merges_base_snapshot(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-base-foo.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/Foo",
                "kind": "resource",
                "type": "Foo",
                "snapshot": {
                    "element": [
                        {"path": "Foo"},
                        {"path": "Foo.status", "min": 1, "max": "1", "type": [{"code": "code"}]},
                        {"path": "Foo.note", "min": 0, "max": "1", "type": [{"code": "string"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-derived-foo.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/DerivedFoo",
                "kind": "resource",
                "type": "Foo",
                "baseDefinition": "http://example.test/StructureDefinition/Foo",
                "differential": {
                    "element": [
                        {"path": "Foo.note", "min": 1, "max": "1", "type": [{"code": "string"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    report = Validator(ValidatorConfig(local_package_paths=[str(package_dir)])).validate_resource({"resourceType": "Foo", "status": "ok"})
    assert any(issue.code == "cardinality.min" and issue.path == "Foo.note" for issue in report.issues)


def test_configured_package_is_resolved_from_source_and_recorded(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    structure_definition = source_dir / "StructureDefinition-Bar.json"
    structure_definition.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/Bar",
                "kind": "resource",
                "type": "Bar",
                "snapshot": {
                    "element": [
                        {"path": "Bar"},
                        {"path": "Bar.status", "min": 1, "max": "1", "type": [{"code": "code"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    package_path = tmp_path / "example.bar-1.0.0.tgz"
    with tarfile.open(package_path, "w:gz") as archive:
        archive.add(structure_definition, arcname="package/StructureDefinition-Bar.json")
    config = ValidatorConfig(
        packages=[
            PackageConfig(
                name="example.bar",
                version="1.0.0",
                source=str(package_path),
            )
        ],
        package_cache_dir=str(tmp_path / "cache"),
    )
    report = Validator(config).validate_resource({"resourceType": "Bar"})
    definition_source = report.to_dict()["definitionSource"]
    summary = report.to_dict()["summary"]
    assert definition_source["packages"][0]["name"] == "example.bar"
    assert Path(definition_source["packages"][0]["path"]).exists()
    assert summary["packageVersions"] == [{"name": "example.bar", "version": "1.0.0"}]
    assert any(issue.code == "cardinality.min" and issue.path == "Bar.status" for issue in report.issues)


def test_profile_slicing_value_discriminator_enforces_slice_cardinality(tmp_path: Path) -> None:
    profile = tmp_path / "patient-sliced-identifier.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-sliced-identifier",
                "type": "Patient",
                "differential": {
                    "element": [
                        {
                            "path": "Patient.identifier",
                            "min": 0,
                            "max": "*",
                            "slicing": {
                                "discriminator": [{"type": "value", "path": "system"}],
                                "rules": "open",
                            },
                        },
                        {"path": "Patient.identifier", "sliceName": "mrn", "min": 1, "max": "1"},
                        {
                            "path": "Patient.identifier.system",
                            "sliceName": "mrn",
                            "min": 1,
                            "max": "1",
                            "fixedUri": "https://hospital.example/mrn",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/patient-sliced-identifier"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-sliced-identifier"]}
    patient["identifier"] = [{"system": "https://other.example/id", "value": "123"}]
    missing_report = Validator(config).validate_resource(patient)
    assert any(issue.code == "profile.slice.cardinality.min" for issue in missing_report.issues)

    patient["identifier"] = [
        {"system": "https://hospital.example/mrn", "value": "123"},
        {"system": "https://hospital.example/mrn", "value": "456"},
    ]
    duplicate_report = Validator(config).validate_resource(patient)
    assert any(issue.code == "profile.slice.cardinality.max" for issue in duplicate_report.issues)


def test_profile_slicing_exists_discriminator(tmp_path: Path) -> None:
    profile = tmp_path / "patient-sliced-telecom.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-sliced-telecom",
                "type": "Patient",
                "differential": {
                    "element": [
                        {
                            "path": "Patient.telecom",
                            "slicing": {
                                "discriminator": [{"type": "exists", "path": "value"}],
                                "rules": "open",
                            },
                        },
                        {"path": "Patient.telecom", "sliceName": "with-value", "min": 1, "max": "*"},
                        {"path": "Patient.telecom.value", "sliceName": "with-value", "min": 1, "max": "1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(profile)], profiles={"Patient": ["http://example.test/StructureDefinition/patient-sliced-telecom"]})
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-sliced-telecom"]}
    patient["telecom"] = [{"system": "phone"}]
    report = Validator(config).validate_resource(patient)
    assert any(issue.code == "profile.slice.cardinality.min" for issue in report.issues)


def test_malformed_package_resources_are_skipped_without_crashing(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "not-json.json").write_text("{", encoding="utf-8")
    (package_dir / "StructureDefinition-Malformed.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "kind": "resource",
                "type": "Malformed",
                "snapshot": {
                    "element": [
                        {"path": "Malformed"},
                        {"path": "Malformed.status", "min": "not-an-int", "max": "bad", "type": [{"code": "code"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    bad_archive = tmp_path / "bad.tgz"
    bad_archive.write_bytes(b"not a gzip archive")

    report = Validator(ValidatorConfig(local_package_paths=[str(package_dir), str(bad_archive)])).validate_resource(
        {"resourceType": "Malformed", "status": "ok"}
    )

    assert report.resource_count == 1
    assert not any(issue.code == "json.invalid" for issue in report.issues)


def test_package_resource_definitions_load_on_first_use(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-LazyType.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "kind": "resource",
                "type": "LazyType",
                "snapshot": {
                    "element": [
                        {"path": "LazyType"},
                        {"path": "LazyType.status", "min": 1, "max": "1", "type": [{"code": "code"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    validator = Validator(ValidatorConfig(local_package_paths=[str(package_dir)]))

    assert "LazyType" not in validator.resource_definitions
    report = validator.validate_resource({"resourceType": "LazyType", "status": "ok"})

    assert report.status is Status.PASS
    assert "LazyType" in validator.resource_definitions


def test_package_value_sets_expand_lazily_and_cache(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "CodeSystem-Test.json").write_text(
        json.dumps(
            {
                "resourceType": "CodeSystem",
                "url": "http://example.test/CodeSystem/test",
                "concept": [{"code": "a"}, {"code": "b"}],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "ValueSet-Test.json").write_text(
        json.dumps(
            {
                "resourceType": "ValueSet",
                "url": "http://example.test/ValueSet/test",
                "compose": {"include": [{"system": "http://example.test/CodeSystem/test"}]},
            }
        ),
        encoding="utf-8",
    )
    resolver = TerminologyResolver(ValidatorConfig().terminology, [str(package_dir)])

    assert resolver.evidence()["loadedValueSets"] == 1
    assert resolver.evidence()["expandedPackageValueSets"] == 0
    assert resolver.contains("http://example.test/ValueSet/test", "a") is True
    assert resolver.evidence()["expandedPackageValueSets"] == 2
    assert resolver.contains("test", "missing") is False
    assert resolver.evidence()["expandedPackageValueSets"] == 2


def test_package_download_retries_transient_failures(tmp_path: Path, monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return b"package bytes"

    calls = {"count": 0}

    def flaky_urlopen(source: str, timeout: int):
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary")
        return Response()

    monkeypatch.setattr("pyfhircheck.profiles.package.urlopen", flaky_urlopen)

    resolved = PackageResolver(tmp_path).resolve(PackageConfig(name="example.retry", version="1.0.0"))

    assert calls["count"] == 3
    assert Path(resolved.path).read_bytes() == b"package bytes"


def test_large_bundle_validation_smoke() -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": f"p-{index}", "gender": "female"}}
            for index in range(1000)
        ],
    }

    report = Validator().validate_resource(bundle)

    assert report.resource_count == 1001
    assert report.to_dict()["summary"]["resourcesByType"] == {"Bundle": 1, "Patient": 1000}


def test_backbone_element_recursive_validation(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-WithBackbone.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "kind": "resource",
                "type": "WithBackbone",
                "snapshot": {
                    "element": [
                        {"path": "WithBackbone"},
                        {"path": "WithBackbone.status", "min": 1, "max": "1", "type": [{"code": "code"}]},
                        {"path": "WithBackbone.contact", "min": 0, "max": "*", "type": [{"code": "BackboneElement"}]},
                        {"path": "WithBackbone.contact.name", "min": 1, "max": "1", "type": [{"code": "string"}]},
                        {"path": "WithBackbone.contact.phone", "min": 0, "max": "1", "type": [{"code": "string"}]},
                        {"path": "WithBackbone.contact.address", "min": 0, "max": "1", "type": [{"code": "BackboneElement"}]},
                        {"path": "WithBackbone.contact.address.city", "min": 1, "max": "1", "type": [{"code": "string"}]},
                        {"path": "WithBackbone.contact.address.zip", "min": 0, "max": "1", "type": [{"code": "string"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(local_package_paths=[str(package_dir)])
    validator = Validator(config)

    valid = {"resourceType": "WithBackbone", "status": "active", "contact": [{"name": "Alice", "address": {"city": "Berlin"}}]}
    report = validator.validate_resource(valid)
    assert report.status is Status.PASS

    missing_required = {"resourceType": "WithBackbone", "status": "active", "contact": [{"phone": "123"}]}
    report = validator.validate_resource(missing_required)
    codes = {issue.code for issue in report.issues}
    assert "cardinality.min" in codes

    unknown_field = {"resourceType": "WithBackbone", "status": "active", "contact": [{"name": "Alice", "badField": "x"}]}
    report = validator.validate_resource(unknown_field)
    assert any(issue.code == "element.unknown" and "badField" in issue.path for issue in report.issues)

    nested_missing = {"resourceType": "WithBackbone", "status": "active", "contact": [{"name": "Alice", "address": {"zip": "10115"}}]}
    report = validator.validate_resource(nested_missing)
    assert any(issue.code == "cardinality.min" and "city" in issue.path for issue in report.issues)


def test_transitive_dependency_resolution(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "StructureDefinition-Base.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "kind": "resource",
                "type": "DepTest",
                "url": "http://example.test/StructureDefinition/DepTest",
                "snapshot": {
                    "element": [
                        {"path": "DepTest"},
                        {"path": "DepTest.status", "min": 1, "max": "1", "type": [{"code": "code"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (base_dir / "package.json").write_text(
        json.dumps({"name": "example.base", "version": "1.0.0", "dependencies": {}}),
        encoding="utf-8",
    )
    base_tgz = tmp_path / "example.base-1.0.0.tgz"
    with tarfile.open(base_tgz, "w:gz") as archive:
        for f in base_dir.iterdir():
            archive.add(f, arcname=f"package/{f.name}")

    ig_dir = tmp_path / "ig"
    ig_dir.mkdir()
    (ig_dir / "StructureDefinition-IGProfile.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/ig-profile",
                "type": "DepTest",
                "baseDefinition": "http://example.test/StructureDefinition/DepTest",
                "differential": {
                    "element": [{"path": "DepTest.status", "fixedCode": "active"}]
                },
            }
        ),
        encoding="utf-8",
    )
    (ig_dir / "package.json").write_text(
        json.dumps({"name": "example.ig", "version": "2.0.0", "dependencies": {"example.base": "1.0.0"}}),
        encoding="utf-8",
    )
    ig_tgz = tmp_path / "example.ig-2.0.0.tgz"
    with tarfile.open(ig_tgz, "w:gz") as archive:
        for f in ig_dir.iterdir():
            archive.add(f, arcname=f"package/{f.name}")

    config = ValidatorConfig(
        packages=[
            PackageConfig(name="example.ig", version="2.0.0", source=str(ig_tgz)),
            PackageConfig(name="example.base", version="1.0.0", source=str(base_tgz)),
        ],
        package_cache_dir=str(cache_dir),
        profiles={"DepTest": ["http://example.test/StructureDefinition/ig-profile"]},
    )
    validator = Validator(config)
    report = validator.validate_resource(
        {
            "resourceType": "DepTest",
            "status": "inactive",
            "meta": {"profile": ["http://example.test/StructureDefinition/ig-profile"]},
        }
    )
    assert report.to_dict()["definitionSource"]["packages"][0]["name"] == "example.base"
    assert report.to_dict()["definitionSource"]["packages"][1]["name"] == "example.ig"
    assert any(issue.code == "profile.fixed" or issue.code == "profile.element.fixed" for issue in report.issues)


def test_three_level_snapshot_resolution(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-base-animal.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/Animal",
                "kind": "resource",
                "type": "Animal",
                "snapshot": {
                    "element": [
                        {"path": "Animal"},
                        {"path": "Animal.species", "min": 1, "max": "1", "type": [{"code": "string"}]},
                        {"path": "Animal.name", "min": 0, "max": "1", "type": [{"code": "string"}]},
                        {"path": "Animal.weight", "min": 0, "max": "1", "type": [{"code": "decimal"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-pet.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/Pet",
                "type": "Animal",
                "baseDefinition": "http://example.test/StructureDefinition/Animal",
                "differential": {
                    "element": [
                        {"path": "Animal.name", "min": 1, "max": "1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-dog.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/Dog",
                "type": "Animal",
                "baseDefinition": "http://example.test/StructureDefinition/Pet",
                "differential": {
                    "element": [
                        {"path": "Animal.species", "fixedString": "dog"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(
        local_package_paths=[str(package_dir)],
        profiles={"Animal": ["http://example.test/StructureDefinition/Dog"]},
    )
    animal = {
        "resourceType": "Animal",
        "species": "cat",
        "meta": {"profile": ["http://example.test/StructureDefinition/Dog"]},
    }
    report = Validator(config).validate_resource(animal)
    codes = {issue.code for issue in report.issues}
    assert "profile.element.fixed" in codes or "profile.fixed" in codes
    assert any(
        (issue.code == "profile.cardinality.min" or issue.code == "profile.element.cardinality.min")
        and "name" in (issue.path or "")
        for issue in report.issues
    )
    assert report.to_dict()["definitionSource"]["mergedSnapshots"] >= 2


def test_binding_tightening_in_snapshot_merge(tmp_path: Path) -> None:
    package_dir = tmp_path / "ig"
    package_dir.mkdir()
    (package_dir / "StructureDefinition-base-obs.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/BaseObs",
                "kind": "resource",
                "type": "Obs",
                "snapshot": {
                    "element": [
                        {"path": "Obs"},
                        {
                            "path": "Obs.status",
                            "min": 1,
                            "max": "1",
                            "type": [{"code": "code"}],
                            "binding": {"strength": "extensible", "valueSet": "http://example.test/ValueSet/obs-status"},
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "StructureDefinition-strict-obs.json").write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/StrictObs",
                "type": "Obs",
                "baseDefinition": "http://example.test/StructureDefinition/BaseObs",
                "differential": {
                    "element": [
                        {
                            "path": "Obs.status",
                            "binding": {"strength": "required", "valueSet": "http://example.test/ValueSet/obs-status"},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "ValueSet-obs-status.json").write_text(
        json.dumps(
            {
                "resourceType": "ValueSet",
                "url": "http://example.test/ValueSet/obs-status",
                "compose": {"include": [{"system": "http://example.test/cs", "concept": [{"code": "final"}, {"code": "amended"}]}]},
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(
        local_package_paths=[str(package_dir)],
        profiles={"Obs": ["http://example.test/StructureDefinition/StrictObs"]},
    )
    report = Validator(config).validate_resource(
        {"resourceType": "Obs", "status": "invalid-code", "meta": {"profile": ["http://example.test/StructureDefinition/StrictObs"]}}
    )
    assert any(
        "required" in issue.code and "binding" in issue.code
        for issue in report.issues
    )


def test_type_discriminator_matches_by_element_type(tmp_path: Path) -> None:
    profile = tmp_path / "obs-sliced-value.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/obs-sliced-value",
                "type": "Observation",
                "differential": {
                    "element": [
                        {
                            "path": "Observation.value[x]",
                            "slicing": {
                                "discriminator": [{"type": "type", "path": "$this"}],
                                "rules": "open",
                            },
                        },
                        {
                            "path": "Observation.value[x]",
                            "sliceName": "valueQuantity",
                            "min": 1,
                            "max": "1",
                            "type": [{"code": "Quantity"}],
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(
        local_package_paths=[str(profile)],
        profiles={"Observation": ["http://example.test/StructureDefinition/obs-sliced-value"]},
    )
    obs_with_quantity = {
        "resourceType": "Observation",
        "id": "o1",
        "status": "final",
        "code": {"text": "test"},
        "valueQuantity": {"value": 42, "unit": "mg"},
        "meta": {"profile": ["http://example.test/StructureDefinition/obs-sliced-value"]},
    }
    report = Validator(config).validate_resource(obs_with_quantity)
    slice_issues = [i for i in report.issues if "slice" in i.code]
    assert not any(i.code == "profile.slice.cardinality.min" for i in slice_issues)

    obs_with_string = {
        "resourceType": "Observation",
        "id": "o2",
        "status": "final",
        "code": {"text": "test"},
        "valueString": "no match",
        "meta": {"profile": ["http://example.test/StructureDefinition/obs-sliced-value"]},
    }
    report = Validator(config).validate_resource(obs_with_string)
    assert any(i.code == "profile.slice.cardinality.min" for i in report.issues)


def test_nested_pattern_discriminator_deep_path(tmp_path: Path) -> None:
    profile = tmp_path / "patient-identifier-typed.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-identifier-typed",
                "type": "Patient",
                "differential": {
                    "element": [
                        {
                            "path": "Patient.identifier",
                            "slicing": {
                                "discriminator": [{"type": "value", "path": "type.coding.system"}],
                                "rules": "open",
                            },
                        },
                        {"path": "Patient.identifier", "sliceName": "kvnr", "min": 1, "max": "1"},
                        {
                            "path": "Patient.identifier.type.coding.system",
                            "sliceName": "kvnr",
                            "fixedUri": "http://fhir.de/CodeSystem/identifier-type-de-basis",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(
        local_package_paths=[str(profile)],
        profiles={"Patient": ["http://example.test/StructureDefinition/patient-identifier-typed"]},
    )
    patient_valid = valid_patient()
    patient_valid["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-identifier-typed"]}
    patient_valid["identifier"] = [
        {
            "type": {"coding": [{"system": "http://fhir.de/CodeSystem/identifier-type-de-basis", "code": "GKV"}]},
            "system": "http://fhir.de/sid/gkv/kvid-10",
            "value": "A123456789",
        }
    ]
    report = Validator(config).validate_resource(patient_valid)
    assert not any(i.code == "profile.slice.cardinality.min" for i in report.issues)

    patient_wrong = valid_patient()
    patient_wrong["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-identifier-typed"]}
    patient_wrong["identifier"] = [
        {"system": "http://other.example/id", "value": "X99"}
    ]
    report = Validator(config).validate_resource(patient_wrong)
    assert any(i.code == "profile.slice.cardinality.min" for i in report.issues)


def test_closed_slicing_rejects_unmatched_elements(tmp_path: Path) -> None:
    profile = tmp_path / "patient-closed-telecom.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/patient-closed-telecom",
                "type": "Patient",
                "differential": {
                    "element": [
                        {
                            "path": "Patient.telecom",
                            "slicing": {
                                "discriminator": [{"type": "value", "path": "system"}],
                                "rules": "closed",
                            },
                        },
                        {"path": "Patient.telecom", "sliceName": "phone", "min": 0, "max": "*"},
                        {"path": "Patient.telecom.system", "sliceName": "phone", "fixedCode": "phone"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(
        local_package_paths=[str(profile)],
        profiles={"Patient": ["http://example.test/StructureDefinition/patient-closed-telecom"]},
    )
    patient_ok = valid_patient()
    patient_ok["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-closed-telecom"]}
    patient_ok["telecom"] = [{"system": "phone", "value": "555-1234"}]
    report = Validator(config).validate_resource(patient_ok)
    assert not any(i.code == "profile.slice.closed" for i in report.issues)

    patient_bad = valid_patient()
    patient_bad["meta"] = {"profile": ["http://example.test/StructureDefinition/patient-closed-telecom"]}
    patient_bad["telecom"] = [
        {"system": "phone", "value": "555-1234"},
        {"system": "email", "value": "foo@bar.com"},
    ]
    report = Validator(config).validate_resource(patient_bad)
    assert any(i.code == "profile.slice.closed" for i in report.issues)


def test_slice_element_binding_enforcement(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "ValueSet-system-vs.json").write_text(
        json.dumps(
            {
                "resourceType": "ValueSet",
                "url": "http://example.test/ValueSet/telecom-system",
                "compose": {"include": [{"system": "http://hl7.org/fhir/contact-point-system", "concept": [{"code": "phone"}, {"code": "fax"}]}]},
            }
        ),
        encoding="utf-8",
    )
    profile = package_dir / "StructureDefinition-bound-telecom.json"
    profile.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/bound-telecom",
                "type": "Patient",
                "differential": {
                    "element": [
                        {
                            "path": "Patient.telecom",
                            "slicing": {
                                "discriminator": [{"type": "exists", "path": "value"}],
                                "rules": "open",
                            },
                        },
                        {"path": "Patient.telecom", "sliceName": "main", "min": 1, "max": "1"},
                        {"path": "Patient.telecom.value", "sliceName": "main", "min": 1, "max": "1"},
                        {
                            "path": "Patient.telecom.system",
                            "sliceName": "main",
                            "min": 1,
                            "max": "1",
                            "binding": {"strength": "required", "valueSet": "http://example.test/ValueSet/telecom-system"},
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config = ValidatorConfig(
        local_package_paths=[str(package_dir)],
        profiles={"Patient": ["http://example.test/StructureDefinition/bound-telecom"]},
    )
    patient = valid_patient()
    patient["meta"] = {"profile": ["http://example.test/StructureDefinition/bound-telecom"]}
    patient["telecom"] = [{"system": "email", "value": "foo@bar.com"}]
    report = Validator(config).validate_resource(patient)
    assert any("binding" in i.code and "required" in i.code for i in report.issues)
