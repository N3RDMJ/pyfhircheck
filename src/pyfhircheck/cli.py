from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from pyfhircheck.config import ValidatorConfig
from pyfhircheck.conformance import run_conformance_cases
from pyfhircheck.core.engine import Validator
from pyfhircheck.core.util import file_sha256, iter_json_files
from pyfhircheck.evidence.drift import compare_reports
from pyfhircheck.evidence.store import EvidenceStore
from pyfhircheck.exceptions import PyFhircheckError
from pyfhircheck.models import Status, ValidationReport
from pyfhircheck.profiles.package import PackageResolver
from pyfhircheck.reporting.output import agent_report, ci_summary, console_summary, json_report, operation_outcome
from pyfhircheck.rules.catalog import explain_rule, rule_catalog


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "compare":
            return _compare(args)
        if args.command == "export-evidence":
            return _export(args)
        if args.command == "rules":
            print(json.dumps(rule_catalog(), indent=2, sort_keys=True))
            return 0
        if args.command == "explain":
            explanation = explain_rule(args.code)
            if args.json:
                print(json.dumps(explanation, indent=2, sort_keys=True))
            else:
                print(f"{explanation['code']}: {explanation['hint']}")
                print(f"category={explanation['category']} repairability={explanation['repairability']} skill={explanation['skill']}")
            return 0
        config = ValidatorConfig.load(getattr(args, "config", None))
        config_errors = config.validate()
        if args.command == "validate-config":
            if config_errors:
                for error in config_errors:
                    print(error)
                return 2
            print("Config valid")
            return 0
        if args.command == "package-fetch":
            if config_errors:
                for error in config_errors:
                    print(error)
                return 2
            resolved = PackageResolver(config.package_cache_dir).resolve_all(config.packages)
            print(json.dumps([package.to_dict() for package in resolved], indent=2, sort_keys=True))
            return 0
        if args.command == "conformance":
            result = run_conformance_cases(Path(args.path), config)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["failed"] == 0 else 1
        if config_errors:
            for error in config_errors:
                print(error)
            return 2
        validator = Validator(config)
        if args.command in {"file", "bundle", "folder"}:
            path = Path(args.path)
            changed_files = _changed_files(path, args.changed_from) if args.changed_from else None
            if changed_files is not None:
                report = validator.validate_files(changed_files, f"{path} changed since {args.changed_from}", reference_file_paths=iter_json_files(path))
            else:
                report = validator.validate_path(path)
        elif args.command == "server":
            report = validator.validate_server(args.url)
        else:
            parser.print_help()
            return 2
        report.replay["validatorCommand"] = _replay_command(args)
        evidence_path = EvidenceStore(config.evidence_output_dir).write(report, argv=_replay_command(args))
        _write_requested_outputs(args, report)
        max_issues = 1 if getattr(args, "fail_fast", False) else getattr(args, "max_issues", None)
        if getattr(args, "agent_output", False):
            print(agent_report(report, str(evidence_path), max_issues=max_issues))
        else:
            print(console_summary(report, max_issues=max_issues))
            print(f"Evidence: {evidence_path}")
        return _exit_for(report, config)
    except PyFhircheckError as exc:
        print(f"Validator error: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI maps unexpected runtime errors to exit code 2.
        print(f"Validator error: {exc}")
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyfhircheck")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (("file", "validate one resource file"), ("bundle", "validate one Bundle file"), ("folder", "validate a folder of resources")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("path")
        _common(cmd)
    server = sub.add_parser("server", help="validate resources fetched from a FHIR server")
    server.add_argument("url")
    _common(server)
    cfg = sub.add_parser("validate-config", help="validate pyfhircheck config")
    cfg.add_argument("-c", "--config")
    pkg = sub.add_parser("package-fetch", help="resolve configured FHIR packages into the local cache")
    pkg.add_argument("-c", "--config")
    conf = sub.add_parser("conformance", help="run a directory of expected PASS/WARN/FAIL validation cases")
    conf.add_argument("path")
    conf.add_argument("-c", "--config")
    compare = sub.add_parser("compare", help="compare two validation evidence runs")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument("--fail-on-new-errors", action="store_true")
    export = sub.add_parser("export-evidence", help="copy an evidence run to another directory")
    export.add_argument("run")
    export.add_argument("destination")
    rules = sub.add_parser("rules", help="print the machine-readable validation rule catalog")
    rules.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    explain = sub.add_parser("explain", help="explain a validation rule code")
    explain.add_argument("code")
    explain.add_argument("--json", action="store_true")
    return parser


def _common(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("-c", "--config")
    cmd.add_argument("--json-output")
    cmd.add_argument("--operation-outcome-output")
    cmd.add_argument("--ci-summary-output")
    cmd.add_argument("--agent-output", action="store_true", help="write a single machine-readable JSON object to stdout")
    cmd.add_argument("--max-issues", type=int, help="limit issues shown in console or agent output")
    cmd.add_argument("--fail-fast", action="store_true", help="show only the first issue in loop-oriented output")
    cmd.add_argument("--changed-from", help="validate only JSON files changed since a previous evidence run")


def _write_requested_outputs(args: argparse.Namespace, report: ValidationReport) -> None:
    if args.json_output:
        Path(args.json_output).write_text(json_report(report), encoding="utf-8")
    if args.operation_outcome_output:
        Path(args.operation_outcome_output).write_text(json.dumps(operation_outcome(report), indent=2, sort_keys=True), encoding="utf-8")
    if args.ci_summary_output:
        Path(args.ci_summary_output).write_text(ci_summary(report) + "\n", encoding="utf-8")


def _exit_for(report: ValidationReport, config: ValidatorConfig) -> int:
    if report.status is Status.FAIL:
        return 1
    if config.ci_failure_threshold == "warning" and report.status is Status.WARN:
        return 1
    return 0


def _compare(args: argparse.Namespace) -> int:
    before = EvidenceStore.load_report(args.before)
    after = EvidenceStore.load_report(args.after)
    result = compare_reports(before, after)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if args.fail_on_new_errors and result["summary"]["newErrors"] else 0


def _export(args: argparse.Namespace) -> int:
    source = Path(args.run)
    if source.is_file():
        source = source.parent
    destination = Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    print(target)
    return 0


def _changed_files(path: Path, evidence_run: str) -> list[Path]:
    previous = EvidenceStore.load_report(evidence_run)
    previous_inputs = previous.get("inputs", {})
    if not isinstance(previous_inputs, dict) or not previous_inputs:
        return iter_json_files(path)
    changed = []
    for file_path in iter_json_files(path):
        old_hash = previous_inputs.get(str(file_path))
        try:
            current_hash = file_sha256(file_path)
        except OSError:
            changed.append(file_path)
            continue
        if current_hash != old_hash:
            changed.append(file_path)
    return changed


def _replay_command(args: argparse.Namespace) -> list[str]:
    command = [str(args.command)]
    for attr in ("path", "url"):
        value = getattr(args, attr, None)
        if value:
            command.append(str(value))
    if getattr(args, "config", None):
        command.extend(["--config", str(args.config)])
    if getattr(args, "changed_from", None):
        command.extend(["--changed-from", str(args.changed_from)])
    return command
