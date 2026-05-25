from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from pyfhircheck.config import ValidatorConfig
from pyfhircheck.conformance import run_conformance_cases
from pyfhircheck.core.engine import Validator
from pyfhircheck.evidence.drift import compare_reports
from pyfhircheck.evidence.store import EvidenceStore
from pyfhircheck.models import Status
from pyfhircheck.profiles.package import PackageResolver
from pyfhircheck.reporting.output import ci_summary, console_summary, json_report, operation_outcome


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
            report = validator.validate_path(Path(args.path))
        elif args.command == "server":
            report = validator.validate_server(args.url)
        else:
            parser.print_help()
            return 2
        evidence_path = EvidenceStore(config.evidence_output_dir).write(report)
        _write_requested_outputs(args, report)
        print(console_summary(report))
        print(f"Evidence: {evidence_path}")
        return _exit_for(report, config)
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
    return parser


def _common(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("-c", "--config")
    cmd.add_argument("--json-output")
    cmd.add_argument("--operation-outcome-output")
    cmd.add_argument("--ci-summary-output")


def _write_requested_outputs(args: argparse.Namespace, report) -> None:
    if args.json_output:
        Path(args.json_output).write_text(json_report(report), encoding="utf-8")
    if args.operation_outcome_output:
        Path(args.operation_outcome_output).write_text(json.dumps(operation_outcome(report), indent=2, sort_keys=True), encoding="utf-8")
    if args.ci_summary_output:
        Path(args.ci_summary_output).write_text(ci_summary(report) + "\n", encoding="utf-8")


def _exit_for(report, config: ValidatorConfig) -> int:
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
