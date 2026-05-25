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


def test_cli_agent_output_is_machine_readable(tmp_path: Path, capsys) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"resourceType": "Patient", "id": "bad id", "gender": "bad"}), encoding="utf-8")
    config = tmp_path / "pyfhircheck.json"
    config.write_text(json.dumps({"evidenceOutputDir": str(tmp_path / "evidence")}), encoding="utf-8")

    assert main(["file", str(invalid), "-c", str(config), "--agent-output", "--max-issues", "1"]) == 1

    output = json.loads(capsys.readouterr().out)
    assert output["schemaVersion"] == "pyfhircheck.agent-output.v1"
    assert output["status"] == "FAIL"
    assert output["truncated"] is True
    assert len(output["topIssues"]) == 1
    assert output["topIssues"][0]["fingerprint"]
    assert output["topIssues"][0]["rule"]["skill"]
    assert Path(output["evidencePath"], "manifest.json").exists()


def test_cli_changed_from_validates_only_changed_files(tmp_path: Path) -> None:
    folder = tmp_path / "resources"
    folder.mkdir()
    first = folder / "a.json"
    second = folder / "b.json"
    first.write_text(json.dumps({"resourceType": "Patient", "id": "a", "gender": "female"}), encoding="utf-8")
    second.write_text(json.dumps({"resourceType": "Patient", "id": "b", "gender": "female"}), encoding="utf-8")
    config = tmp_path / "pyfhircheck.json"
    config.write_text(json.dumps({"evidenceOutputDir": str(tmp_path / "evidence")}), encoding="utf-8")

    assert main(["folder", str(folder), "-c", str(config)]) == 0
    previous_run = next((tmp_path / "evidence").iterdir())
    second.write_text(json.dumps({"resourceType": "Patient", "id": "bad id", "gender": "bad"}), encoding="utf-8")
    changed_report = tmp_path / "changed.json"

    assert main(["folder", str(folder), "-c", str(config), "--changed-from", str(previous_run), "--json-output", str(changed_report)]) == 1

    data = json.loads(changed_report.read_text(encoding="utf-8"))
    assert data["resourceCount"] == 1
    assert list(data["inputs"]) == [str(second)]


def test_cli_changed_from_uses_unchanged_files_for_reference_context(tmp_path: Path) -> None:
    folder = tmp_path / "resources"
    folder.mkdir()
    patient = folder / "patient.json"
    encounter = folder / "encounter.json"
    patient.write_text(json.dumps({"resourceType": "Patient", "id": "p1", "gender": "female"}), encoding="utf-8")
    encounter.write_text(
        json.dumps(
            {
                "resourceType": "Encounter",
                "id": "e1",
                "status": "finished",
                "class": {"code": "AMB"},
                "subject": {"reference": "Patient/p1"},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "pyfhircheck.json"
    config.write_text(json.dumps({"evidenceOutputDir": str(tmp_path / "evidence")}), encoding="utf-8")

    assert main(["folder", str(folder), "-c", str(config)]) == 0
    previous_run = next((tmp_path / "evidence").iterdir())
    encounter.write_text(
        json.dumps(
            {
                "resourceType": "Encounter",
                "id": "e1",
                "status": "finished",
                "class": {"code": "AMB"},
                "subject": {"reference": "Patient/p1"},
                "period": {"start": "2026-01-01"},
            }
        ),
        encoding="utf-8",
    )
    changed_report = tmp_path / "changed.json"

    assert main(["folder", str(folder), "-c", str(config), "--changed-from", str(previous_run), "--json-output", str(changed_report)]) == 0

    data = json.loads(changed_report.read_text(encoding="utf-8"))
    assert data["resourceCount"] == 1
    assert not any(issue["code"] == "reference.unresolved" for issue in data["issues"])


def test_cli_explain_rule(capsys) -> None:
    assert main(["explain", "datatype.invalid", "--json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "datatype.invalid"
    assert output["skill"] == "fhir-datatype"
