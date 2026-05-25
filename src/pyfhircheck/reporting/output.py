from __future__ import annotations

import json
from typing import Any

from pyfhircheck.models import Severity, ValidationIssue, ValidationReport
from pyfhircheck.rules.catalog import explain_rule


def json_report(report: ValidationReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def console_summary(report: ValidationReport, max_issues: int | None = None) -> str:
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
    limit = 20 if max_issues is None else max(0, max_issues)
    for issue in report.issues[:limit]:
        lines.append(f"- {issue.severity.value.upper()} {issue.code} {issue.path or ''}: {issue.message}")
    if len(report.issues) > limit:
        lines.append(f"... {len(report.issues) - limit} more issue(s)")
    return "\n".join(lines)


def agent_report(report: ValidationReport, evidence_path: str | None = None, max_issues: int | None = None) -> str:
    data = report.to_dict()
    top_issues = report.issues if max_issues is None else report.issues[: max(0, max_issues)]
    payload = {
        "schemaVersion": "pyfhircheck.agent-output.v1",
        "status": report.status.value,
        "exitCode": 1 if report.status.value == "FAIL" else 0,
        "runId": report.run_id,
        "evidencePath": evidence_path,
        "counts": data["counts"],
        "deterministicHash": report.deterministic_hash,
        "inputSource": report.input_source,
        "inputs": report.input_hashes,
        "truncated": max_issues is not None and len(report.issues) > max_issues,
        "topIssues": [_agent_issue(issue) for issue in top_issues],
        "issueGroups": _issue_groups(report.issues, max_issues),
        "nextCommand": _next_command(report, evidence_path),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _agent_issue(issue: ValidationIssue) -> dict[str, Any]:
    data = issue.to_dict()
    data["fingerprint"] = issue.fingerprint()
    data["rule"] = explain_rule(issue.code)
    return data


def _issue_groups(issues: list[ValidationIssue], max_groups: int | None) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for issue in issues:
        key = (issue.severity.value, issue.code, issue.path or "", issue.profile or "")
        group = groups.setdefault(
            key,
            {
                "severity": issue.severity.value,
                "code": issue.code,
                "path": issue.path,
                "profile": issue.profile,
                "count": 0,
                "rule": explain_rule(issue.code),
                "examples": [],
            },
        )
        group["count"] += 1
        if len(group["examples"]) < 3:
            group["examples"].append(issue.to_dict())
    ordered = sorted(groups.values(), key=lambda item: (_severity_rank(str(item["severity"])), str(item["code"]), str(item["path"])))
    return ordered if max_groups is None else ordered[: max(0, max_groups)]


def _severity_rank(severity: str) -> int:
    return {"error": 0, "warning": 1, "information": 2}.get(severity, 3)


def _next_command(report: ValidationReport, evidence_path: str | None) -> str | None:
    if report.status.value == "PASS":
        return None
    if evidence_path:
        return f"pyfhircheck export-evidence {evidence_path} exported-evidence"
    return None


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
    outcome_issue = {
        "severity": issue.severity.value,
        "code": _issue_type(issue),
        "diagnostics": issue.diagnostics or issue.message,
        "details": _issue_details(issue),
        "expression": [issue.path] if issue.path else [],
    }
    if issue.path:
        outcome_issue["location"] = [_xpath_location(issue.path)]
    return outcome_issue


def _issue_details(issue: ValidationIssue) -> dict[str, Any]:
    coding = [{"system": "https://pyfhircheck.local/rules", "code": issue.code, "display": issue.message}]
    text = issue.message
    if issue.profile:
        coding.append({"system": "https://pyfhircheck.local/profiles", "code": issue.profile, "display": "Validated profile"})
        text = f"{issue.message} Profile: {issue.profile}"
    return {"coding": coding, "text": text}


def _issue_type(issue: ValidationIssue) -> str:
    if issue.code.startswith("json."):
        return "structure"
    if issue.code.startswith(("datatype.", "choice.", "contained.")):
        return "invalid"
    if issue.code.startswith("terminology") or ".binding." in issue.code:
        return "code-invalid"
    if issue.code.startswith(("profile.", "extension.", "bundle.", "reference.", "custom.")):
        return "business-rule"
    if issue.severity is Severity.ERROR:
        return "invalid"
    return "business-rule"


def _xpath_location(path: str) -> str:
    location = path
    if "." in location:
        resource_type, remainder = location.split(".", 1)
        location = f"/f:{resource_type}/" + "/".join(f"f:{part}" for part in remainder.split(".") if part)
    else:
        location = f"/f:{location}"
    return location.replace("[", "[").replace("]", "]")
