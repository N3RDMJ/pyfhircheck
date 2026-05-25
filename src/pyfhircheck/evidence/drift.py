from __future__ import annotations

from typing import Any


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_issues = {_fingerprint(issue): issue for issue in before.get("issues", [])}
    after_issues = {_fingerprint(issue): issue for issue in after.get("issues", [])}
    before_keys = set(before_issues)
    after_keys = set(after_issues)
    changed_severities = []
    for key in before_keys & after_keys:
        if before_issues[key].get("severity") != after_issues[key].get("severity"):
            changed_severities.append({"before": before_issues[key], "after": after_issues[key]})
    changed_config = {
        "profilesChanged": before.get("configuredProfiles") != after.get("configuredProfiles"),
        "igsChanged": before.get("configuredIGs") != after.get("configuredIGs"),
        "terminologyChanged": before.get("terminology") != after.get("terminology"),
        "definitionSourceChanged": before.get("definitionSource") != after.get("definitionSource"),
        "hashChanged": before.get("deterministicHash") != after.get("deterministicHash"),
    }
    new_errors = [after_issues[key] for key in sorted(after_keys - before_keys) if after_issues[key].get("severity") == "error"]
    return {
        "beforeRunId": before.get("runId"),
        "afterRunId": after.get("runId"),
        "newIssues": [after_issues[key] for key in sorted(after_keys - before_keys)],
        "resolvedIssues": [before_issues[key] for key in sorted(before_keys - after_keys)],
        "newErrors": new_errors,
        "changedSeverities": changed_severities,
        "changedConfig": changed_config,
        "summary": {
            "newErrors": len(new_errors),
            "newIssues": len(after_keys - before_keys),
            "resolvedIssues": len(before_keys - after_keys),
            "changedSeverities": len(changed_severities),
            "status": "FAIL" if new_errors else "PASS",
        },
    }


def _fingerprint(issue: dict[str, Any]) -> str:
    return "|".join(
        str(issue.get(key) or "")
        for key in ("code", "resourceType", "resourceId", "path", "profile", "source")
    )
