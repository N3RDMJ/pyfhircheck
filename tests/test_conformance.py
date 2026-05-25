from __future__ import annotations

import json
from pathlib import Path

from pyfhircheck.config import ValidatorConfig
from pyfhircheck.cli import main
from pyfhircheck.conformance import run_conformance_cases


def test_conformance_harness_reports_pass_rate(tmp_path: Path) -> None:
    (tmp_path / "valid.json").write_text(
        json.dumps({"expectedStatus": "PASS", "resource": {"resourceType": "Patient", "id": "p1", "gender": "female"}}),
        encoding="utf-8",
    )
    (tmp_path / "invalid.json").write_text(
        json.dumps({"expectedStatus": "FAIL", "resource": {"resourceType": "Patient", "id": "bad id"}}),
        encoding="utf-8",
    )
    result = run_conformance_cases(tmp_path)
    assert result["total"] == 2
    assert result["passed"] == 2
    assert result["passRate"] == 1.0


def test_cli_conformance_exit_code(tmp_path: Path) -> None:
    (tmp_path / "case.json").write_text(
        json.dumps({"expectedStatus": "PASS", "resource": {"resourceType": "Patient", "id": "bad id"}}),
        encoding="utf-8",
    )
    assert main(["conformance", str(tmp_path)]) == 1


def test_conformance_matches_expected_issues(tmp_path: Path) -> None:
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "expectedStatus": "FAIL",
                "forbidUnexpectedIssues": True,
                "resource": {"resourceType": "Patient", "id": "bad id"},
                "expectedIssues": [
                    {
                        "severity": "error",
                        "code": "datatype.invalid",
                        "path": "Patient.id",
                        "source": "datatype",
                        "messageContains": "must be id",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_conformance_cases(tmp_path)
    assert result["passed"] == 1
    assert result["results"][0]["matchedIssues"] == 1


def test_conformance_matches_operation_outcome_expected_issue(tmp_path: Path) -> None:
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "expectedStatus": "FAIL",
                "resource": {"resourceType": "Patient", "id": "bad id"},
                "expectedOperationOutcome": {
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "invalid",
                            "details": {
                                "coding": [
                                    {
                                        "system": "https://pyfhircheck.local/rules",
                                        "code": "datatype.invalid",
                                    }
                                ]
                            },
                            "expression": ["Patient.id"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    result = run_conformance_cases(tmp_path)
    assert result["passed"] == 1
    assert result["results"][0]["missingIssues"] == []


def test_conformance_reports_missing_expected_issue(tmp_path: Path) -> None:
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "expectedStatus": "FAIL",
                "resource": {"resourceType": "Patient", "id": "bad id"},
                "expectedIssues": [{"severity": "error", "code": "terminology.required"}],
            }
        ),
        encoding="utf-8",
    )
    result = run_conformance_cases(tmp_path)
    assert result["failed"] == 1
    assert result["results"][0]["missingIssues"] == [{"severity": "error", "code": "terminology.required"}]


def test_conformance_case_can_reference_fixture_path(tmp_path: Path) -> None:
    fixture = tmp_path / "fixtures" / "patient.json"
    fixture.parent.mkdir()
    fixture.write_text(json.dumps({"resourceType": "Patient", "id": "p1"}), encoding="utf-8")
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "patient.case.json").write_text(
        json.dumps({"expectedStatus": "PASS", "fixturePath": "../fixtures/patient.json"}),
        encoding="utf-8",
    )

    result = run_conformance_cases(case_dir)

    assert result["passed"] == 1


def test_isik3_basismodul_config_preset_loads() -> None:
    config = ValidatorConfig.load(Path("examples/isik3-basismodul.json"))

    assert config.validate() == []
    assert config.packages[0].name == "de.gematik.isik-basismodul"
    assert config.packages[0].version == "3.1.1"
    assert "http://snomed.info/sct" in config.terminology.ignored_code_systems
    assert config.profiles["Patient"] == ["https://gematik.de/fhir/isik/v3/Basismodul/StructureDefinition/ISiKPatient"]
    assert config.error_on_unknown_profile is False
    assert config.allow_unknown_extensions is True


def test_isik3_basismodul_conformance_cases_cover_fixture_tree() -> None:
    fixtures = sorted(Path("tests/fixtures/isik3-basismodul").rglob("*.json"))
    cases = sorted(Path("tests/conformance/isik3-basismodul").rglob("*.case.json"))

    assert len(fixtures) == 33
    assert len(cases) == len(fixtures)
