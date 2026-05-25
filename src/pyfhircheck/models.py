from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFORMATION = "information"
    WARNING = "warning"
    ERROR = "error"


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    resource_type: str | None = None
    resource_id: str | None = None
    path: str | None = None
    profile: str | None = None
    diagnostics: str | None = None
    source: str = "pyfhircheck"

    def fingerprint(self) -> str:
        parts = [
            self.code,
            self.resource_type or "",
            self.resource_id or "",
            self.path or "",
            self.profile or "",
            self.source,
        ]
        return "|".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "resourceType": self.resource_type,
            "resourceId": self.resource_id,
            "path": self.path,
            "profile": self.profile,
            "diagnostics": self.diagnostics,
            "source": self.source,
        }


@dataclass(frozen=True)
class ResourceValidationSummary:
    index: int
    resource_type: str | None
    resource_id: str | None
    profiles: tuple[str, ...] = ()
    status: Status = Status.PASS
    issue_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    information_count: int = 0
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def reference(self) -> str:
        if self.resource_type and self.resource_id:
            return f"{self.resource_type}/{self.resource_id}"
        if self.resource_type:
            return f"{self.resource_type}[{self.index}]"
        return f"Resource[{self.index}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "reference": self.reference,
            "resourceType": self.resource_type,
            "resourceId": self.resource_id,
            "profiles": list(self.profiles),
            "status": self.status.value,
            "issueCount": self.issue_count,
            "counts": {
                "errors": self.error_count,
                "warnings": self.warning_count,
                "information": self.information_count,
            },
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class ValidationReport:
    run_id: str
    timestamp: str
    validator_version: str
    fhir_version: str
    input_source: str
    resource_count: int
    configured_profiles: dict[str, list[str]]
    configured_igs: list[str]
    terminology: dict[str, Any]
    deterministic_hash: str
    definition_source: dict[str, Any] = field(default_factory=dict)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    input_hashes: dict[str, str] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)
    resources: list[ResourceValidationSummary] = field(default_factory=list)
    status: Status = Status.PASS

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    @property
    def information(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.INFORMATION]

    def to_dict(self) -> dict[str, Any]:
        resources = [resource.to_dict() for resource in self.resources]
        return {
            "runId": self.run_id,
            "timestamp": self.timestamp,
            "validatorVersion": self.validator_version,
            "fhirVersion": self.fhir_version,
            "inputSource": self.input_source,
            "resourceCount": self.resource_count,
            "configuredProfiles": self.configured_profiles,
            "configuredIGs": self.configured_igs,
            "terminology": self.terminology,
            "definitionSource": self.definition_source,
            "configSnapshot": self.config_snapshot,
            "inputs": self.input_hashes,
            "replay": self.replay,
            "counts": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "information": len(self.information),
            },
            "summary": self._summary(resources),
            "status": self.status.value,
            "deterministicHash": self.deterministic_hash,
            "resources": resources,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def _summary(self, resources: list[dict[str, Any]]) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        by_profile: dict[str, int] = {}
        status_by_resource_type: dict[str, dict[str, int]] = {}
        for resource in resources:
            resource_type = resource.get("resourceType") or "unknown"
            by_type[resource_type] = by_type.get(resource_type, 0) + 1
            status_counts = status_by_resource_type.setdefault(resource_type, {"PASS": 0, "WARN": 0, "FAIL": 0})
            status = str(resource.get("status", "PASS"))
            status_counts[status] = status_counts.get(status, 0) + 1
            for profile in resource.get("profiles", []):
                by_profile[profile] = by_profile.get(profile, 0) + 1
        packages = [
            {"name": package.get("name"), "version": package.get("version")}
            for package in self.definition_source.get("packages", [])
            if isinstance(package, dict)
        ]
        return {
            "totalResources": self.resource_count,
            "resourcesByType": dict(sorted(by_type.items())),
            "resourcesByProfile": dict(sorted(by_profile.items())),
            "statusByResourceType": dict(sorted(status_by_resource_type.items())),
            "packageVersions": packages,
        }
