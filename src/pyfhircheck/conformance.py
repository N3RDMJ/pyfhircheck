from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyfhircheck.config import ValidatorConfig
from pyfhircheck.core.engine import Validator
from pyfhircheck.models import Status


@dataclass(frozen=True)
class ConformanceCaseResult:
    path: str
    expected: str
    actual: str
    passed: bool
    issue_count: int
    expected_issues: int = 0
    matched_issues: int = 0
    missing_issues: tuple[dict[str, Any], ...] = ()
    unexpected_issues: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "issueCount": self.issue_count,
            "expectedIssues": self.expected_issues,
            "matchedIssues": self.matched_issues,
            "missingIssues": list(self.missing_issues),
            "unexpectedIssues": list(self.unexpected_issues),
        }


def run_conformance_cases(path: Path, config: ValidatorConfig | None = None) -> dict[str, Any]:
    validator = Validator(config)
    results: list[ConformanceCaseResult] = []
    for case_path in sorted(path.rglob("*.json")) if path.is_dir() else [path]:
        data = json.loads(case_path.read_text(encoding="utf-8"))
        resource = data.get("resource") if isinstance(data, dict) and "resource" in data else data
        expected = data.get("expectedStatus", data.get("expected", "PASS")) if isinstance(data, dict) else "PASS"
        if expected == "ERROR":
            expected = "FAIL"
        report = validator.validate_resource(resource, str(case_path))
        actual = _normalize(report.status)
        expected = str(expected).upper()
        expected_issues = _expected_issues(data if isinstance(data, dict) else {})
        matched, missing = _match_expected_issues(expected_issues, report.to_dict()["issues"])
        unexpected = _unexpected_issues(data if isinstance(data, dict) else {}, report.to_dict()["issues"])
        passed = expected == actual and not missing and not unexpected
        results.append(
            ConformanceCaseResult(
                path=str(case_path),
                expected=expected,
                actual=actual,
                passed=passed,
                issue_count=len(report.issues),
                expected_issues=len(expected_issues),
                matched_issues=matched,
                missing_issues=tuple(missing),
                unexpected_issues=tuple(unexpected),
            )
        )
    passed = sum(1 for result in results if result.passed)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "passRate": passed / len(results) if results else 0.0,
        "results": [result.to_dict() for result in results],
    }


def _normalize(status: Status) -> str:
    if status is Status.FAIL:
        return "FAIL"
    if status is Status.WARN:
        return "WARN"
    return "PASS"


def _expected_issues(case: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(case.get("expectedIssues"), list):
        return [issue for issue in case["expectedIssues"] if isinstance(issue, dict)]
    outcome = case.get("expectedOperationOutcome") or case.get("operationOutcome")
    if isinstance(outcome, dict) and isinstance(outcome.get("issue"), list):
        return [_from_operation_outcome_issue(issue) for issue in outcome["issue"] if isinstance(issue, dict)]
    return []


def _from_operation_outcome_issue(issue: dict[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    if isinstance(issue.get("severity"), str):
        expected["severity"] = issue["severity"]
    details = issue.get("details", {})
    codings = details.get("coding", []) if isinstance(details, dict) else []
    for coding in codings:
        if isinstance(coding, dict) and isinstance(coding.get("code"), str):
            expected["code"] = coding["code"]
            break
    expression = issue.get("expression")
    if isinstance(expression, list) and expression and isinstance(expression[0], str):
        expected["path"] = expression[0]
    diagnostics = issue.get("diagnostics")
    if isinstance(diagnostics, str):
        expected["messageContains"] = diagnostics
    return expected


def _match_expected_issues(expected_issues: list[dict[str, Any]], actual_issues: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    matched = 0
    missing: list[dict[str, Any]] = []
    used: set[int] = set()
    for expected in expected_issues:
        match_index = next(
            (
                index
                for index, actual in enumerate(actual_issues)
                if index not in used and _issue_matches(expected, actual)
            ),
            None,
        )
        if match_index is None:
            missing.append(expected)
        else:
            used.add(match_index)
            matched += 1
    return matched, missing


def _unexpected_issues(case: dict[str, Any], actual_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not case.get("forbidUnexpectedIssues", False):
        return []
    expected = _expected_issues(case)
    return [
        actual
        for actual in actual_issues
        if not any(_issue_matches(item, actual) for item in expected)
    ]


def _issue_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for field in ("severity", "code", "path", "source", "resourceType", "resourceId", "profile"):
        if field in expected and expected[field] != actual.get(field):
            return False
    contains = expected.get("messageContains")
    if isinstance(contains, str):
        message = actual.get("message") or actual.get("diagnostics") or ""
        if contains not in message:
            return False
    return True
