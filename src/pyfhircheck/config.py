from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyfhircheck.exceptions import ConfigError


def _config_str(data: dict[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return default


def _config_str_list(data: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
    return []


def _config_str_dict(data: dict[str, Any], *keys: str) -> dict[str, list[str]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            return {
                str(code_system): [code for code in codes if isinstance(code, str)]
                for code_system, codes in value.items()
                if isinstance(codes, list)
            }
    return {}


def _config_profiles(data: dict[str, Any]) -> dict[str, list[str]]:
    value = data.get("profiles")
    if not isinstance(value, dict):
        return {}
    profiles: dict[str, list[str]] = {}
    for resource_type, urls in value.items():
        if isinstance(urls, list):
            profiles[str(resource_type)] = [url for url in urls if isinstance(url, str)]
    return profiles


def _config_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


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
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigError(f"Config file not found: {config_path}")
        try:
            raw = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Invalid JSON in config file {config_path}: {exc.msg} at line {exc.lineno}, column {exc.colno}"
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(f"Config file {config_path} must contain a JSON object")
        terminology_raw = data.get("terminology", {})
        terminology = terminology_raw if isinstance(terminology_raw, dict) else {}
        return cls(
            fhir_version=_config_str(data, "fhirVersion", "fhir_version", default="4.0.1"),
            enabled_igs=_config_str_list(data, "enabledIGs", "enabled_igs"),
            packages=[
                PackageConfig(
                    name=str(item.get("name")),
                    version=str(item.get("version", "latest")),
                    registry=str(item.get("registry", "https://packages.fhir.org")),
                    source=item.get("source") if isinstance(item.get("source"), str) else None,
                )
                for item in data.get("packages", [])
                if isinstance(item, dict) and item.get("name")
            ],
            package_cache_dir=_config_str(data, "packageCacheDir", "package_cache_dir", default=".pyfhircheck/packages"),
            local_package_paths=_config_str_list(data, "localPackagePaths", "local_package_paths"),
            remote_package_sources=_config_str_list(data, "remotePackageSources", "remote_package_sources"),
            terminology=TerminologyConfig(
                mode=_config_str(terminology, "mode", default="local"),
                code_systems=_config_str_dict(terminology, "codeSystems", "code_systems"),
                ignored_code_systems=_config_str_list(terminology, "ignoredCodeSystems", "ignored_code_systems"),
                ignored_value_sets=_config_str_list(terminology, "ignoredValueSets", "ignored_value_sets"),
            ),
            profiles=_config_profiles(data),
            error_on_unknown_profile=bool(data.get("errorOnUnknownProfile", data.get("error_on_unknown_profile", True))),
            allow_unknown_extensions=bool(data.get("allowUnknownExtensions", data.get("allow_unknown_extensions", False))),
            severity_policy={
                str(rule): severity
                for rule, severity in (data.get("severityPolicy", data.get("severity_policy", {})) or {}).items()
                if isinstance(severity, str)
            },
            ci_failure_threshold=_config_str(data, "ciFailureThreshold", "ci_failure_threshold", default="error"),
            custom_rules=_config_dict(data, "customRules", "custom_rules"),
            evidence_output_dir=_config_str(data, "evidenceOutputDir", "evidence_output_dir", default="evidence"),
            server_validation_targets=_config_str_list(data, "serverValidationTargets", "server_validation_targets"),
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
