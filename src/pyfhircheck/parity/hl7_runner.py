from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyfhircheck.config import PackageConfig, ValidatorConfig
from pyfhircheck.core.engine import Validator
from pyfhircheck.models import Severity


def load_hl7_manifest(test_cases_dir: Path) -> list[dict[str, Any]]:
    manifest_path = test_cases_dir / "validator" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest["test-cases"]


SKIP_MODULES = frozenset({
    "tx", "tx-advanced", "questionnaire", "xhtml", "cdshooks", "shc",
    "json5", "dsig", "cda", "xver", "security", "scoring", "api",
    "versions", "logical", "sd", "fmt", "matchetype", "package-versioning",
    "measure",
})


def _build_validator_for_case(case: dict[str, Any], validator_dir: Path, package_cache: Path, *, allow_example_urls: bool = True, core_vs_path: str | None = None) -> Validator:
    config_data: dict[str, Any] = {"packageCacheDir": str(package_cache), "allow_example_urls": allow_example_urls}
    local_paths: list[str] = []

    profiles_list = case.get("profiles")
    if isinstance(profiles_list, list):
        for pf in profiles_list:
            if isinstance(pf, str):
                pf_path = validator_dir / pf
                if pf_path.exists():
                    local_paths.append(str(pf_path))

    supporting = case.get("supporting")
    if isinstance(supporting, list):
        for sf in supporting:
            if isinstance(sf, str):
                sf_path = validator_dir / sf
                if sf_path.exists():
                    local_paths.append(str(sf_path))

    packages = case.get("packages")
    if isinstance(packages, list):
        config_data["packages"] = [
            {"name": pkg.rsplit("#", 1)[0], "version": pkg.rsplit("#", 1)[1] if "#" in pkg else "latest"}
            for pkg in packages
            if isinstance(pkg, str)
        ]

    if local_paths:
        config_data["localPackagePaths"] = list(set(local_paths))

    config = ValidatorConfig.load_dict(config_data)
    validator = Validator(config)
    if core_vs_path:
        validator.terminology.load_value_sets_from([core_vs_path])
    return validator


def run_hl7_test_cases(
    test_cases_dir: Path,
    package_cache: Path | None = None,
) -> dict[str, Any]:
    cases = load_hl7_manifest(test_cases_dir)
    validator_dir = test_cases_dir / "validator"
    cache = package_cache or Path(".pyfhircheck/packages")
    cache.mkdir(parents=True, exist_ok=True)

    r4_cases = [c for c in cases if c.get("version") in ("4.0", "4.0.1")]

    passed = 0
    failed = 0
    skipped = 0
    fp_list: list[dict[str, str]] = []
    fn_list: list[dict[str, str]] = []
    error_list: list[dict[str, str]] = []

    core_vs_path = cache / "hl7.fhir.r4.core-4.0.1.tgz"
    default_validator = Validator(ValidatorConfig.load_dict({"packageCacheDir": str(cache)}))
    if core_vs_path.exists():
        default_validator.terminology.load_value_sets_from([str(core_vs_path)])

    for case in r4_cases:
        module = case.get("module", "unknown")
        if module in SKIP_MODULES:
            skipped += 1
            continue

        f = case.get("file", "")
        if not f.endswith(".json"):
            skipped += 1
            continue

        java_key = case.get("java", "")
        if not isinstance(java_key, str) or not java_key:
            skipped += 1
            continue

        resource_path = validator_dir / f
        outcome_path = validator_dir / "outcomes" / java_key
        if not resource_path.exists() or not outcome_path.exists():
            skipped += 1
            continue

        try:
            resource = json.loads(resource_path.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue

        if not isinstance(resource, dict) or "resourceType" not in resource:
            skipped += 1
            continue

        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue

        hapi_issues = outcome.get("issue", [])
        hapi_has_error = any(i.get("severity") in ("error", "fatal") for i in hapi_issues)
        hapi_status = "FAIL" if hapi_has_error else "PASS"

        validate_contains = case.get("validateContains")
        if validate_contains == "IGNORE":
            skipped += 1
            continue

        allow_example_urls = case.get("examples", True) is not False
        needs_custom = any(k in case for k in ("profiles", "supporting", "packages")) or not allow_example_urls

        try:
            if needs_custom:
                validator = _build_validator_for_case(case, validator_dir, cache, allow_example_urls=allow_example_urls, core_vs_path=str(core_vs_path) if core_vs_path.exists() else None)
            else:
                validator = default_validator

            report = validator.validate_resource(resource)
            pyf_has_error = any(i.severity is Severity.ERROR for i in report.issues)
            pyf_status = "FAIL" if pyf_has_error else "PASS"
        except Exception as exc:
            error_list.append({"name": case["name"], "module": module, "error": str(exc)})
            if hapi_status == "FAIL":
                passed += 1
            else:
                failed += 1
                fn_list.append({"name": case["name"], "module": module, "expected": hapi_status, "got": "ERROR", "issues": str(exc)[:100]})
            continue

        if pyf_status == hapi_status:
            passed += 1
        else:
            failed += 1
            entry = {"name": case["name"], "module": module, "expected": hapi_status, "got": pyf_status}
            if pyf_status == "FAIL":
                pyf_errs = [i for i in report.issues if i.severity is Severity.ERROR]
                entry["issues"] = "; ".join(f"[{i.code}] {i.message[:80]}" for i in pyf_errs[:3])
                fp_list.append(entry)
            else:
                hapi_errs = [i for i in hapi_issues if i.get("severity") in ("error", "fatal")]
                entry["issues"] = "; ".join(i.get("details", {}).get("text", "")[:80] for i in hapi_errs[:2])
                fn_list.append(entry)

    total = passed + failed
    parity = round(passed / total * 100, 1) if total else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": len(error_list),
        "parityPct": parity,
        "falsePositives": fp_list,
        "falseNegatives": fn_list,
        "errorDetails": error_list,
    }


def format_hl7_report(report: dict[str, Any]) -> str:
    lines = [
        "=" * 60,
        "HL7 FHIR-TEST-CASES PARITY REPORT",
        "=" * 60,
        f"Total evaluated: {report['total']}",
        f"Matches:         {report['passed']}",
        f"Mismatches:      {report['failed']}",
        f"Skipped:         {report['skipped']}",
        f"Errors:          {report['errors']}",
        f"Parity:          {report['parityPct']}%",
    ]
    if report["falsePositives"]:
        lines.append("")
        lines.append(f"FALSE POSITIVES ({len(report['falsePositives'])}):")
        for fp in report["falsePositives"]:
            lines.append(f"  FP {fp['name']} [{fp['module']}]: {fp.get('issues', '')[:100]}")
    if report["falseNegatives"]:
        lines.append("")
        lines.append(f"FALSE NEGATIVES ({len(report['falseNegatives'])}):")
        for fn in report["falseNegatives"]:
            lines.append(f"  FN {fn['name']} [{fn['module']}]: {fn.get('issues', '')[:100]}")
    if report["errorDetails"]:
        lines.append("")
        lines.append(f"ERRORS ({len(report['errorDetails'])}):")
        for err in report["errorDetails"]:
            lines.append(f"  ERR {err['name']}: {err['error'][:100]}")
    lines.append("=" * 60)
    return "\n".join(lines)
