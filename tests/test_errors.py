from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyfhircheck.cli import main
from pyfhircheck.config import ValidatorConfig
from pyfhircheck.conformance import run_conformance_cases
from pyfhircheck.evidence.store import EvidenceStore
from pyfhircheck.exceptions import ConformanceError, ConfigError, EvidenceError, PackageError
from pyfhircheck.profiles.package import PackageResolver


def test_config_load_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ConfigError, match="Config file not found"):
        ValidatorConfig.load(missing)


def test_config_load_invalid_json(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid JSON in config file"):
        ValidatorConfig.load(config)


def test_config_load_non_object(tmp_path: Path) -> None:
    config = tmp_path / "array.json"
    config.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="must contain a JSON object"):
        ValidatorConfig.load(config)


def test_evidence_load_missing_report(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="Evidence report not found"):
        EvidenceStore.load_report(tmp_path / "missing")


def test_evidence_load_invalid_json(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("{bad", encoding="utf-8")
    with pytest.raises(EvidenceError, match="Invalid JSON in evidence report"):
        EvidenceStore.load_report(report)


def test_package_copy_missing_local_source(tmp_path: Path) -> None:
    config = tmp_path / "pyfhircheck.json"
    config.write_text(
        json.dumps(
            {
                "packageCacheDir": str(tmp_path / "cache"),
                "packages": [{"name": "example.missing", "version": "1.0.0", "source": str(tmp_path / "missing.tgz")}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="Package source not found"):
        PackageResolver(json.loads(config.read_text(encoding="utf-8"))["packageCacheDir"]).resolve_all(
            ValidatorConfig.load(config).packages
        )


def test_conformance_invalid_case_json(tmp_path: Path) -> None:
    case = tmp_path / "case.json"
    case.write_text("{bad", encoding="utf-8")
    with pytest.raises(ConformanceError, match="Invalid JSON in conformance case"):
        run_conformance_cases(case)


def test_conformance_missing_fixture(tmp_path: Path) -> None:
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"fixturePath": "missing.json", "expected": "PASS"}), encoding="utf-8")
    with pytest.raises(ConformanceError, match="Conformance fixture not found"):
        run_conformance_cases(case)


def test_cli_reports_config_error(tmp_path: Path, capsys) -> None:
    config = tmp_path / "bad.json"
    config.write_text("{bad", encoding="utf-8")
    assert main(["validate-config", "-c", str(config)]) == 2
    assert "Invalid JSON in config file" in capsys.readouterr().out


def test_cli_reports_evidence_error(tmp_path: Path, capsys) -> None:
    assert main(["compare", str(tmp_path / "before"), str(tmp_path / "after")]) == 2
    assert "Evidence report not found" in capsys.readouterr().out
