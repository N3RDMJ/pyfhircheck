from __future__ import annotations

import json
from typing import Any

from pyfhircheck.models import Severity, ValidationIssue, ValidationReport


def json_report(report: ValidationReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def console_summary(report: ValidationReport) -> str:
    data = report.to_dict()
    lines = [
        f"pyfhircheck {report.validator_version} | FHIR {report.fhir_version}",
        f"Run: {report.run_id}",
        f"Input: {report.input_source}",
        f"Resources: {report.resource_count}",
        f"Status: {report.status.value}",
        f"Errors: {data['counts']['errors']}  Warnings: {data['counts']['warnings']}  Info: {data['counts']['information']}",
        f"Hash: {report.deterministic_hash}",
    ]
    if report.definition_source:
        lines.append(
            "Definitions: "
            f"{report.definition_source.get('mode')} "
            f"({report.definition_source.get('loadedStructureDefinitions', 0)} StructureDefinitions)"
        )
    for issue in report.issues[:20]:
        lines.append(f"- {issue.severity.value.upper()} {issue.code} {issue.path or ''}: {issue.message}")
    if len(report.issues) > 20:
        lines.append(f"... {len(report.issues) - 20} more issue(s)")
    return "\n".join(lines)


def ci_summary(report: ValidationReport) -> str:
    counts = report.to_dict()["counts"]
    return f"status={report.status.value} resources={report.resource_count} errors={counts['errors']} warnings={counts['warnings']} hash={report.deterministic_hash}"


def operation_outcome(report: ValidationReport) -> dict[str, Any]:
    return {
        "resourceType": "OperationOutcome",
        "id": report.run_id,
        "issue": [_outcome_issue(issue) for issue in report.issues],
    }


def _outcome_issue(issue: ValidationIssue) -> dict[str, Any]:
    code = "invalid" if issue.severity is Severity.ERROR else "business-rule"
    return {
        "severity": issue.severity.value,
        "code": code,
        "diagnostics": issue.diagnostics or issue.message,
        "details": {"coding": [{"system": "https://pyfhircheck.local/rules", "code": issue.code, "display": issue.message}]},
        "expression": [issue.path] if issue.path else [],
    }
