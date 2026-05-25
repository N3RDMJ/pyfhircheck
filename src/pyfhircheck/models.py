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
            self.severity.value,
            self.code,
            self.resource_type or "",
            self.resource_id or "",
            self.path or "",
            self.profile or "",
            self.message,
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
    issues: list[ValidationIssue] = field(default_factory=list)
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
            "counts": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "information": len(self.information),
            },
            "status": self.status.value,
            "deterministicHash": self.deterministic_hash,
            "issues": [issue.to_dict() for issue in self.issues],
        }
