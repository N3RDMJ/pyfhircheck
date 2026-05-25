from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TerminologyConfig:
    mode: str = "local"
    code_systems: dict[str, list[str]] = field(default_factory=dict)
    ignored_code_systems: list[str] = field(default_factory=list)
    ignored_value_sets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "codeSystems": self.code_systems,
            "ignoredCodeSystems": self.ignored_code_systems,
            "ignoredValueSets": self.ignored_value_sets,
        }


@dataclass
class PackageConfig:
    name: str
    version: str = "latest"
    registry: str = "https://packages.fhir.org"
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"name": self.name, "version": self.version, "registry": self.registry}
        if self.source:
            data["source"] = self.source
        return data


@dataclass
class ValidatorConfig:
    fhir_version: str = "4.0.1"
    enabled_igs: list[str] = field(default_factory=list)
    packages: list[PackageConfig] = field(default_factory=list)
    package_cache_dir: str = ".pyfhircheck/packages"
    local_package_paths: list[str] = field(default_factory=list)
    remote_package_sources: list[str] = field(default_factory=list)
    terminology: TerminologyConfig = field(default_factory=TerminologyConfig)
    profiles: dict[str, list[str]] = field(default_factory=dict)
    error_on_unknown_profile: bool = True
    allow_unknown_extensions: bool = False
    severity_policy: dict[str, str] = field(default_factory=dict)
    ci_failure_threshold: str = "error"
    custom_rules: dict[str, Any] = field(default_factory=dict)
    evidence_output_dir: str = "evidence"
    server_validation_targets: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None) -> "ValidatorConfig":
        if path is None:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        terminology = data.get("terminology", {})
        return cls(
            fhir_version=data.get("fhirVersion", data.get("fhir_version", "4.0.1")),
            enabled_igs=list(data.get("enabledIGs", data.get("enabled_igs", []))),
            packages=[
                PackageConfig(
                    name=str(item.get("name")),
                    version=str(item.get("version", "latest")),
                    registry=str(item.get("registry", "https://packages.fhir.org")),
                    source=item.get("source"),
                )
                for item in data.get("packages", [])
                if isinstance(item, dict) and item.get("name")
            ],
            package_cache_dir=data.get("packageCacheDir", data.get("package_cache_dir", ".pyfhircheck/packages")),
            local_package_paths=list(data.get("localPackagePaths", data.get("local_package_paths", []))),
            remote_package_sources=list(data.get("remotePackageSources", data.get("remote_package_sources", []))),
            terminology=TerminologyConfig(
                mode=terminology.get("mode", "local"),
                code_systems=dict(terminology.get("codeSystems", terminology.get("code_systems", {}))),
                ignored_code_systems=list(terminology.get("ignoredCodeSystems", terminology.get("ignored_code_systems", []))),
                ignored_value_sets=list(terminology.get("ignoredValueSets", terminology.get("ignored_value_sets", []))),
            ),
            profiles=dict(data.get("profiles", {})),
            error_on_unknown_profile=bool(data.get("errorOnUnknownProfile", data.get("error_on_unknown_profile", True))),
            allow_unknown_extensions=bool(data.get("allowUnknownExtensions", data.get("allow_unknown_extensions", False))),
            severity_policy=dict(data.get("severityPolicy", data.get("severity_policy", {}))),
            ci_failure_threshold=data.get("ciFailureThreshold", data.get("ci_failure_threshold", "error")),
            custom_rules=dict(data.get("customRules", data.get("custom_rules", {}))),
            evidence_output_dir=data.get("evidenceOutputDir", data.get("evidence_output_dir", "evidence")),
            server_validation_targets=list(data.get("serverValidationTargets", data.get("server_validation_targets", []))),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.fhir_version not in {"4.0.1", "R4"}:
            errors.append("Only FHIR R4/4.0.1 is supported by this validator version.")
        if self.terminology.mode not in {"off", "local", "strict"}:
            errors.append("terminology.mode must be one of: off, local, strict.")
        if self.ci_failure_threshold not in {"warning", "error"}:
            errors.append("ciFailureThreshold must be 'warning' or 'error'.")
        for package in self.packages:
            if not package.name:
                errors.append("packages entries require name.")
            if not package.version:
                errors.append(f"Package {package.name} requires version.")
        for rule, severity in self.severity_policy.items():
            if severity not in {"information", "warning", "error"}:
                errors.append(f"severityPolicy for {rule} must be information, warning, or error.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "fhirVersion": self.fhir_version,
            "enabledIGs": self.enabled_igs,
            "packages": [package.to_dict() for package in self.packages],
            "packageCacheDir": self.package_cache_dir,
            "localPackagePaths": self.local_package_paths,
            "remotePackageSources": self.remote_package_sources,
            "terminology": self.terminology.to_dict(),
            "profiles": self.profiles,
            "errorOnUnknownProfile": self.error_on_unknown_profile,
            "allowUnknownExtensions": self.allow_unknown_extensions,
            "severityPolicy": self.severity_policy,
            "ciFailureThreshold": self.ci_failure_threshold,
            "customRules": self.custom_rules,
            "evidenceOutputDir": self.evidence_output_dir,
            "serverValidationTargets": self.server_validation_targets,
        }
