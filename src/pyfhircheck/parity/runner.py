from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyfhircheck.config import ValidatorConfig
from pyfhircheck.core.engine import Validator
from pyfhircheck.models import Severity
from pyfhircheck.parity.rules import RULE_CLASSES, SupportLevel, issue_code_to_rule_class


HAPI_JAR_NAME = "validator_cli.jar"
HAPI_CACHE_DIR = ".pyfhircheck/hapi"
HAPI_DOWNLOAD_URL = "https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar"


@dataclass(frozen=True)
class CorpusCase:
    id: str
    description: str
    rule_class: str
    expected_status: str
    expected_issue_codes: tuple[str, ...]
    resource: dict[str, Any]
    profile: str | None = None
    structure_definitions: tuple[dict[str, Any], ...] = ()
    config: dict[str, Any] | None = None
    oracle: dict[str, Any] | None = None
    source_path: str | None = None


@dataclass
class CaseResult:
    case_id: str
    rule_class: str
    rule_support: str
    description: str
    expected_status: str
    pyfhircheck_status: str
    oracle_status: str | None
    status_match: bool
    false_positive: bool
    false_negative: bool
    pyfhircheck_issues: list[dict[str, Any]] = field(default_factory=list)
    oracle_issues: list[dict[str, Any]] = field(default_factory=list)
    matched_issue_count: int = 0
    mismatched_paths: list[str] = field(default_factory=list)
    mismatched_categories: list[str] = field(default_factory=list)
    unsupported: bool = False


def load_corpus(corpus_dir: Path) -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    for case_file in sorted(corpus_dir.rglob("*.case.json")):
        data = json.loads(case_file.read_text(encoding="utf-8"))
        cases.append(CorpusCase(
            id=data["id"],
            description=data.get("description", ""),
            rule_class=data["ruleClass"],
            expected_status=data["expectedStatus"],
            expected_issue_codes=tuple(data.get("expectedIssueCodes", [])),
            resource=data["resource"],
            profile=data.get("profile"),
            structure_definitions=tuple(data.get("structureDefinitions", [])),
            config=data.get("config"),
            oracle=data.get("oracle"),
            source_path=str(case_file),
        ))
    return cases


def _build_validator(case: CorpusCase, tmp_dir: Path) -> Validator:
    config_data: dict[str, Any] = dict(case.config) if case.config else {}
    if case.structure_definitions:
        sd_dir = tmp_dir / "profiles"
        sd_dir.mkdir(exist_ok=True)
        for idx, sd in enumerate(case.structure_definitions):
            (sd_dir / f"sd-{idx}.json").write_text(json.dumps(sd), encoding="utf-8")
        local_paths = list(config_data.get("localPackagePaths", []))
        local_paths.append(str(sd_dir))
        config_data["localPackagePaths"] = local_paths
    if case.profile:
        profiles = dict(config_data.get("profiles", {}))
        resource_type = case.resource.get("resourceType", "")
        profile_list = list(profiles.get(resource_type, []))
        if case.profile not in profile_list:
            profile_list.append(case.profile)
        profiles[resource_type] = profile_list
        config_data["profiles"] = profiles
    config = ValidatorConfig.load_dict(config_data) if config_data else ValidatorConfig()
    return Validator(config)


def _run_pyfhircheck(case: CorpusCase) -> tuple[str, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        validator = _build_validator(case, Path(tmp_dir))
        report = validator.validate_resource(case.resource)
        status = "FAIL" if any(i.severity is Severity.ERROR for i in report.issues) else "PASS"
        issues = [i.to_dict() for i in report.issues]
    return status, issues


def _find_java() -> str | None:
    import shutil
    java = shutil.which("java")
    if java:
        return java
    home_java = Path.home() / ".local" / "java" / "bin" / "java"
    if home_java.exists():
        return str(home_java)
    return None


def _find_hapi_jar() -> Path | None:
    cache = Path(HAPI_CACHE_DIR)
    jar = cache / HAPI_JAR_NAME
    if jar.exists():
        return jar
    return None


def download_hapi(target_dir: str | None = None) -> Path:
    from urllib.request import urlopen

    target = Path(target_dir or HAPI_CACHE_DIR)
    target.mkdir(parents=True, exist_ok=True)
    jar_path = target / HAPI_JAR_NAME
    if jar_path.exists():
        return jar_path
    with urlopen(HAPI_DOWNLOAD_URL, timeout=300) as response:
        jar_path.write_bytes(response.read())
    return jar_path


def run_hapi_oracle(
    resource: dict[str, Any],
    java_path: str,
    jar_path: Path,
    structure_definitions: tuple[dict[str, Any], ...] = (),
    profiles: dict[str, list[str]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        resource_file = tmp / "resource.json"
        resource_file.write_text(json.dumps(resource), encoding="utf-8")
        output_file = tmp / "output.json"
        cmd = [
            java_path, "-jar", str(jar_path),
            str(resource_file),
            "-version", "4.0.1",
            "-output", str(output_file),
            "-output-style", "json",
            "-level", "errors",
        ]
        if structure_definitions:
            ig_dir = tmp / "ig"
            ig_dir.mkdir()
            for idx, sd in enumerate(structure_definitions):
                (ig_dir / f"sd-{idx}.json").write_text(json.dumps(sd), encoding="utf-8")
            cmd.extend(["-ig", str(ig_dir)])
        if profiles:
            for _rt, urls in profiles.items():
                for url in urls:
                    cmd.extend(["-profile", url])
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            return "ERROR", [{"severity": "error", "code": "timeout", "message": "HAPI validator timed out"}]
        if not output_file.exists():
            return "ERROR", [{"severity": "error", "code": "no-output", "message": "HAPI validator produced no output"}]
        outcome = json.loads(output_file.read_text(encoding="utf-8"))
    return _parse_operation_outcome(outcome)


def _parse_operation_outcome(outcome: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    has_error = False
    for issue in outcome.get("issue", []):
        severity = issue.get("severity", "information")
        if severity == "error" or severity == "fatal":
            has_error = True
        parsed: dict[str, Any] = {"severity": severity}
        if isinstance(issue.get("code"), str):
            parsed["code"] = issue["code"]
        expression = issue.get("expression")
        if isinstance(expression, list) and expression:
            parsed["path"] = expression[0]
        elif isinstance(issue.get("location"), list) and issue["location"]:
            parsed["path"] = issue["location"][0]
        diagnostics = issue.get("diagnostics")
        if isinstance(diagnostics, str):
            parsed["message"] = diagnostics
        details = issue.get("details")
        if isinstance(details, dict):
            codings = details.get("coding", [])
            for coding in codings:
                if isinstance(coding, dict) and isinstance(coding.get("code"), str):
                    parsed["detailCode"] = coding["code"]
                    break
        issues.append(parsed)
    return ("FAIL" if has_error else "PASS"), issues


def _compare_issues(
    pyfhircheck_issues: list[dict[str, Any]],
    oracle_issues: list[dict[str, Any]],
) -> tuple[int, list[str], list[str]]:
    matched = 0
    mismatched_paths: list[str] = []
    mismatched_categories: list[str] = []
    used: set[int] = set()
    for oracle_issue in oracle_issues:
        oracle_path = oracle_issue.get("path", "")
        oracle_severity = oracle_issue.get("severity", "")
        best_match = -1
        for idx, pyf_issue in enumerate(pyfhircheck_issues):
            if idx in used:
                continue
            pyf_path = pyf_issue.get("path", "")
            pyf_severity = pyf_issue.get("severity", "")
            if pyf_severity == oracle_severity and _paths_similar(pyf_path, oracle_path):
                best_match = idx
                break
        if best_match >= 0:
            matched += 1
            used.add(best_match)
        else:
            if oracle_severity in ("error", "fatal"):
                mismatched_paths.append(oracle_path)
                mismatched_categories.append(oracle_issue.get("code", "unknown"))
    return matched, mismatched_paths, mismatched_categories


def _paths_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a_norm = a.split("[")[0].rstrip(".")
    b_norm = b.split("[")[0].rstrip(".")
    return a_norm == b_norm or a_norm.endswith(b_norm) or b_norm.endswith(a_norm)


def evaluate_case(case: CorpusCase, *, live_oracle: bool = False, java_path: str | None = None, jar_path: Path | None = None) -> CaseResult:
    rule_info = RULE_CLASSES.get(case.rule_class, {})
    support = rule_info.get("support", SupportLevel.UNSUPPORTED)

    if support is SupportLevel.UNSUPPORTED:
        return CaseResult(
            case_id=case.id,
            rule_class=case.rule_class,
            rule_support=support.value,
            description=case.description,
            expected_status=case.expected_status,
            pyfhircheck_status="SKIP",
            oracle_status=None,
            status_match=False,
            false_positive=False,
            false_negative=False,
            unsupported=True,
        )

    pyf_status, pyf_issues = _run_pyfhircheck(case)

    oracle_status: str | None = None
    oracle_issues: list[dict[str, Any]] = []
    if live_oracle and java_path and jar_path:
        case_profiles = case.config.get("profiles") if case.config else None
        oracle_status, oracle_issues = run_hapi_oracle(
            case.resource, java_path, jar_path,
            structure_definitions=case.structure_definitions,
            profiles=case_profiles,
        )
    elif case.oracle:
        oracle_status = case.oracle.get("status")
        oracle_issues = case.oracle.get("issues", [])

    effective_oracle = oracle_status or case.expected_status
    status_match = pyf_status == effective_oracle
    false_positive = pyf_status == "FAIL" and effective_oracle == "PASS"
    false_negative = pyf_status == "PASS" and effective_oracle == "FAIL"

    matched, m_paths, m_cats = _compare_issues(pyf_issues, oracle_issues)

    return CaseResult(
        case_id=case.id,
        rule_class=case.rule_class,
        rule_support=support.value,
        description=case.description,
        expected_status=case.expected_status,
        pyfhircheck_status=pyf_status,
        oracle_status=oracle_status,
        status_match=status_match,
        false_positive=false_positive,
        false_negative=false_negative,
        pyfhircheck_issues=pyf_issues,
        oracle_issues=oracle_issues,
        matched_issue_count=matched,
        mismatched_paths=m_paths,
        mismatched_categories=m_cats,
    )


def generate_report(results: list[CaseResult]) -> dict[str, Any]:
    total = len(results)
    supported_results = [r for r in results if not r.unsupported]
    unsupported_results = [r for r in results if r.unsupported]

    matches = sum(1 for r in supported_results if r.status_match)
    false_positives = [r for r in supported_results if r.false_positive]
    false_negatives = [r for r in supported_results if r.false_negative]

    all_mismatched_paths = []
    all_mismatched_cats = []
    for r in supported_results:
        all_mismatched_paths.extend(r.mismatched_paths)
        all_mismatched_cats.extend(r.mismatched_categories)

    by_rule: dict[str, dict[str, Any]] = {}
    for r in results:
        entry = by_rule.setdefault(r.rule_class, {"total": 0, "matches": 0, "falsePositives": 0, "falseNegatives": 0, "unsupported": 0, "support": r.rule_support})
        entry["total"] += 1
        if r.unsupported:
            entry["unsupported"] += 1
        elif r.status_match:
            entry["matches"] += 1
        elif r.false_positive:
            entry["falsePositives"] += 1
        elif r.false_negative:
            entry["falseNegatives"] += 1
    for entry in by_rule.values():
        supported = entry["total"] - entry["unsupported"]
        entry["parityPct"] = round(entry["matches"] / supported * 100, 1) if supported else 0.0

    supported_total = len(supported_results)
    parity_pct = round(matches / supported_total * 100, 1) if supported_total else 0.0

    return {
        "totalCases": total,
        "supportedCases": supported_total,
        "unsupportedCases": len(unsupported_results),
        "passFailMatches": matches,
        "falsePositives": len(false_positives),
        "falseNegatives": len(false_negatives),
        "mismatchedPaths": all_mismatched_paths,
        "mismatchedCategories": all_mismatched_cats,
        "parityPct": parity_pct,
        "byRuleClass": dict(sorted(by_rule.items())),
        "falsePositiveDetails": [
            {"caseId": r.case_id, "ruleClass": r.rule_class, "description": r.description}
            for r in false_positives
        ],
        "falseNegativeDetails": [
            {"caseId": r.case_id, "ruleClass": r.rule_class, "description": r.description}
            for r in false_negatives
        ],
        "unsupportedDetails": [
            {"caseId": r.case_id, "ruleClass": r.rule_class, "description": r.description}
            for r in unsupported_results
        ],
        "cases": [
            {
                "caseId": r.case_id,
                "ruleClass": r.rule_class,
                "ruleSupport": r.rule_support,
                "expectedStatus": r.expected_status,
                "pyfhircheckStatus": r.pyfhircheck_status,
                "oracleStatus": r.oracle_status,
                "statusMatch": r.status_match,
                "falsePositive": r.false_positive,
                "falseNegative": r.false_negative,
                "matchedIssues": r.matched_issue_count,
            }
            for r in results
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("PYFHIRCHECK PARITY REPORT")
    lines.append("=" * 60)
    lines.append(f"Total cases:       {report['totalCases']}")
    lines.append(f"Supported cases:   {report['supportedCases']}")
    lines.append(f"Unsupported cases: {report['unsupportedCases']}")
    lines.append(f"Pass/fail matches: {report['passFailMatches']}")
    lines.append(f"False positives:   {report['falsePositives']}")
    lines.append(f"False negatives:   {report['falseNegatives']}")
    lines.append(f"Parity:            {report['parityPct']}%")
    lines.append("")
    lines.append("BY RULE CLASS:")
    lines.append(f"{'Rule Class':<30} {'Support':<20} {'Match':>5} {'FP':>4} {'FN':>4} {'Parity':>8}")
    lines.append("-" * 75)
    for name, info in report["byRuleClass"].items():
        lines.append(
            f"{name:<30} {info['support']:<20} {info['matches']:>5} "
            f"{info['falsePositives']:>4} {info['falseNegatives']:>4} "
            f"{info['parityPct']:>7.1f}%"
        )
    if report["falsePositiveDetails"]:
        lines.append("")
        lines.append("FALSE POSITIVES (pyfhircheck says FAIL, oracle says PASS):")
        for detail in report["falsePositiveDetails"]:
            lines.append(f"  [{detail['caseId']}] {detail['description']}")
    if report["falseNegativeDetails"]:
        lines.append("")
        lines.append("FALSE NEGATIVES (pyfhircheck says PASS, oracle says FAIL):")
        for detail in report["falseNegativeDetails"]:
            lines.append(f"  [{detail['caseId']}] {detail['description']}")
    if report["unsupportedDetails"]:
        lines.append("")
        lines.append("UNSUPPORTED (rule class not implemented):")
        for detail in report["unsupportedDetails"]:
            lines.append(f"  [{detail['caseId']}] {detail['ruleClass']}: {detail['description']}")
    lines.append("=" * 60)
    return "\n".join(lines)


def run_parity(
    corpus_dir: Path,
    *,
    live_oracle: bool = False,
    json_output: Path | None = None,
) -> dict[str, Any]:
    cases = load_corpus(corpus_dir)
    if not cases:
        return {"error": f"No .case.json files found in {corpus_dir}"}

    java_path: str | None = None
    jar_path: Path | None = None
    if live_oracle:
        java_path = _find_java()
        jar_path = _find_hapi_jar()
        if not java_path or not jar_path:
            live_oracle = False

    results = [
        evaluate_case(case, live_oracle=live_oracle, java_path=java_path, jar_path=jar_path)
        for case in cases
    ]
    report = generate_report(results)

    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report
