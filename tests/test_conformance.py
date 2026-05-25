from __future__ import annotations

import json
from pathlib import Path

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
