from __future__ import annotations

from pathlib import Path

import pytest

from pyfhircheck.parity.runner import evaluate_case, generate_report, load_corpus, run_parity

CORPUS_DIR = Path(__file__).parent / "corpus" / "cases"


def test_corpus_loads_all_cases():
    cases = load_corpus(CORPUS_DIR)
    assert len(cases) >= 24


def test_all_cases_match_expected_status():
    cases = load_corpus(CORPUS_DIR)
    failures = []
    for case in cases:
        result = evaluate_case(case)
        if not result.status_match:
            failures.append(f"{case.id}: expected {case.expected_status}, got {result.pyfhircheck_status}")
    assert not failures, "Cases with wrong status:\n" + "\n".join(failures)


def test_parity_report_100_percent():
    report = run_parity(CORPUS_DIR)
    assert report["parityPct"] == 100.0
    assert report["falsePositives"] == 0
    assert report["falseNegatives"] == 0


def test_no_false_negatives():
    report = run_parity(CORPUS_DIR)
    assert report["falseNegativeDetails"] == []


def test_no_false_positives():
    report = run_parity(CORPUS_DIR)
    assert report["falsePositiveDetails"] == []


@pytest.mark.parametrize(
    "case_id",
    [c.id for c in load_corpus(CORPUS_DIR)],
    ids=[c.id for c in load_corpus(CORPUS_DIR)],
)
def test_individual_case(case_id: str):
    cases = load_corpus(CORPUS_DIR)
    case = next(c for c in cases if c.id == case_id)
    result = evaluate_case(case)
    assert result.status_match, (
        f"{case_id}: expected {case.expected_status}, got {result.pyfhircheck_status}"
    )
