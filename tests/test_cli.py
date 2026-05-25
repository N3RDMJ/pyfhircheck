from __future__ import annotations

import json
from pathlib import Path

from pyfhircheck.cli import main


def test_cli_exit_codes_and_json_report(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    report = tmp_path / "report.json"
    valid.write_text(json.dumps({"resourceType": "Patient", "id": "p1", "gender": "female"}), encoding="utf-8")
    invalid.write_text(json.dumps({"resourceType": "Patient", "id": "bad id", "gender": "bad"}), encoding="utf-8")
    assert main(["file", str(valid), "--json-output", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "PASS"
    assert main(["file", str(invalid)]) == 1


def test_cli_compare_runs(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "report.json").write_text(json.dumps({"runId": "a", "issues": []}), encoding="utf-8")
    (after / "report.json").write_text(
        json.dumps({"runId": "b", "issues": [{"severity": "error", "code": "x", "message": "bad"}]}),
        encoding="utf-8",
    )
    assert main(["compare", str(before), str(after), "--fail-on-new-errors"]) == 1


def test_cli_folder_and_validate_config(tmp_path: Path) -> None:
    folder = tmp_path / "resources"
    folder.mkdir()
    (folder / "patient.json").write_text(json.dumps({"resourceType": "Patient", "id": "p1", "gender": "female"}), encoding="utf-8")
    config = tmp_path / "pyfhircheck.json"
    config.write_text(json.dumps({"fhirVersion": "4.0.1", "evidenceOutputDir": str(tmp_path / "evidence")}), encoding="utf-8")
    assert main(["validate-config", "-c", str(config)]) == 0
    assert main(["folder", str(folder), "-c", str(config)]) == 0


def test_cli_package_fetch(tmp_path: Path) -> None:
    package = tmp_path / "pkg.tgz"
    import tarfile

    resource = tmp_path / "StructureDefinition-FetchTest.json"
    resource.write_text(
        json.dumps(
            {
                "resourceType": "StructureDefinition",
                "url": "http://example.test/StructureDefinition/FetchTest",
                "kind": "resource",
                "type": "FetchTest",
                "snapshot": {"element": [{"path": "FetchTest"}]},
            }
        ),
        encoding="utf-8",
    )
    with tarfile.open(package, "w:gz") as archive:
        archive.add(resource, arcname="package/StructureDefinition-FetchTest.json")
    config = tmp_path / "pyfhircheck.json"
    config.write_text(
        json.dumps(
            {
                "packageCacheDir": str(tmp_path / "cache"),
                "packages": [{"name": "example.fetch", "version": "1.0.0", "source": str(package)}],
            }
        ),
        encoding="utf-8",
    )
    assert main(["package-fetch", "-c", str(config)]) == 0
    assert (tmp_path / "cache" / "example.fetch-1.0.0.tgz").exists()
