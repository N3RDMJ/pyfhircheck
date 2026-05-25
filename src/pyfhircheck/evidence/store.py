from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from typing import Any

from pyfhircheck import __version__
from pyfhircheck.exceptions import EvidenceError
from pyfhircheck.models import ValidationReport
from pyfhircheck.reporting.output import operation_outcome


class EvidenceStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write(self, report: ValidationReport, argv: list[str] | None = None) -> Path:
        run_dir = self.root / report.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        (run_dir / "operation-outcome.json").write_text(json.dumps(operation_outcome(report), indent=2, sort_keys=True), encoding="utf-8")
        (run_dir / "ci-summary.txt").write_text(
            f"{report.status.value} errors={len(report.errors)} warnings={len(report.warnings)} hash={report.deterministic_hash}\n",
            encoding="utf-8",
        )
        (run_dir / "config.json").write_text(json.dumps(report.config_snapshot, indent=2, sort_keys=True), encoding="utf-8")
        (run_dir / "inputs.json").write_text(json.dumps(report.input_hashes, indent=2, sort_keys=True), encoding="utf-8")
        manifest = {
            "schemaVersion": "pyfhircheck.evidence-manifest.v1",
            "runId": report.run_id,
            "argv": argv,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "validatorVersion": __version__,
            "report": "report.json",
            "operationOutcome": "operation-outcome.json",
            "ciSummary": "ci-summary.txt",
            "config": "config.json",
            "inputs": "inputs.json",
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return run_dir

    @staticmethod
    def load_report(path: str | Path) -> dict[str, Any]:
        candidate = Path(path)
        if candidate.is_dir():
            candidate = candidate / "report.json"
        if not candidate.is_file():
            raise EvidenceError(f"Evidence report not found: {candidate}")
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvidenceError(f"Could not read evidence report {candidate}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceError(
                f"Invalid JSON in evidence report {candidate}: {exc.msg} at line {exc.lineno}, column {exc.colno}"
            ) from exc
        if not isinstance(data, dict):
            raise EvidenceError(f"Evidence report {candidate} must contain a JSON object")
        return data
