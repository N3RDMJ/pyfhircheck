# pyfhircheck

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Python FHIR R4 validator with evidence, drift detection, and CI-friendly output.**

Validate FHIR JSON resources, Bundles, folders, or live server search results. pyfhircheck goes beyond JSON schema checks: it enforces structure, profiles, terminology, references, Bundle rules, and FHIRPath invariants, then writes reproducible evidence you can compare across runs.

Currently at **79.9% parity** (107/134 matches) with the [HAPI FHIR](https://hapifhir.io/) reference validator on the official [HL7 fhir-test-cases](https://github.com/FHIR/fhir-test-cases) suite. Validation evidence output inspired by [MedVertical Records](https://github.com/medvertical).

## Why pyfhircheck

| | |
|---|---|
| **Deterministic reports** | Every run gets a `runId`, issue fingerprints, config snapshot, and content hash |
| **Evidence on disk** | JSON report, OperationOutcome, CI summary, and manifest under `evidence/<run-id>/` |
| **Drift detection** | Compare two runs for new, resolved, and changed validation issues |
| **Profile-aware** | Load StructureDefinitions from local files, folders, `.tgz` packages, or remote URLs |
| **Automation-ready** | Machine-readable `--agent-output`, rule catalog, and structured logs to stderr |

> [!NOTE]
> pyfhircheck is a working validator with real validation depth covering structure, profiles, terminology, extensions, references, and Bundle semantics. See [HL7 parity](#hl7-parity) for current coverage and [Current limitations](#current-limitations) for known gaps.

## Features

**Validation inputs**

- Single resource file, Bundle, folder of JSON files, or resources fetched from a FHIR server
- Incremental folder validation with `--changed-from` (only re-validate changed files, keep reference context)

**Structure and datatypes**

- JSON validity, `resourceType`, cardinality, choice elements, unknown elements
- Primitive and complex datatype checks for common R4 clinical resources
- Contained resources, modifier extensions, and extension shape validation

**Profiles and packages**

- Enforced profiles from config and `meta.profile`
- FHIR NPM package resolution into a local cache (`package-fetch`)
- Snapshot + differential overlay for effective StructureDefinition elements
- Profile cardinality, fixed/pattern values, bindings, invariants, slicing (value/pattern/exists discriminators), and extension definitions

**Terminology and references**

- Local CodeSystem / ValueSet membership from packages (`terminology.mode`: `off`, `local`, `strict`)
- Reference resolution across contained resources, Bundle `fullUrl`, relative, absolute, and conditional references

**Project rules and conformance**

- Configurable custom rules (identifier systems, local reference resolution, Bundle resource types, and more)
- Conformance fixtures asserting PASS/WARN/FAIL plus expected issues or OperationOutcome-shaped expectations

**Output**

- Console summary, JSON report, OperationOutcome-compatible JSON, CI summary text
- `--agent-output` for a single JSON object with top issues, rule hints, and evidence path
- `compare` and `export-evidence` commands for drift workflows

## Installation

**Requirements:** Python 3.11+ and [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/N3RDMJ/pyfhircheck.git
cd pyfhircheck
uv sync
```

This creates `.venv`, installs locked dependencies from `uv.lock`, and installs pyfhircheck in editable mode with dev tools (pytest, mypy, build).

> [!TIP]
> Without uv, use `pip install -e ".[dev]"` — the project stays compatible with standard PEP 517 tooling.

Build a wheel locally:

```bash
uv sync
uv run python -m build
```

## Quick start

```bash
# Validate a resource
pyfhircheck file examples/valid-patient.json

# Validate with config (profiles, terminology, custom rules)
pyfhircheck file examples/valid-patient.json -c examples/pyfhircheck.json

# Fail CI on validation errors with structured outputs
pyfhircheck folder path/to/resources -c pyfhircheck.json \
  --json-output report.json \
  --ci-summary-output ci-summary.txt
```

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | Validation passed, or warnings below `ciFailureThreshold` |
| `1` | Validation failed (errors, or warnings when threshold is `warning`) |
| `2` | Config, evidence, package, or runtime error |

## CLI reference

| Command | Description |
|---------|-------------|
| `file <path>` | Validate one FHIR JSON resource |
| `bundle <path>` | Validate one Bundle resource |
| `folder <path>` | Validate all `*.json` files in a directory |
| `server <url>` | Validate resources fetched from a FHIR server |
| `validate-config` | Check a config file without validating resources |
| `package-fetch` | Resolve configured FHIR packages into the local cache |
| `conformance <path>` | Run expected PASS/WARN/FAIL fixture cases |
| `compare <before> <after>` | Diff two evidence runs |
| `export-evidence <run> <dest>` | Copy an evidence run to another directory |
| `rules` | Print the machine-readable validation rule catalog |
| `explain <code>` | Explain a validation rule code |

**Common options** (on `file`, `bundle`, `folder`, `server`)

```bash
-c, --config PATH                  Validator config JSON
--json-output PATH                 Write full validation report JSON
--operation-outcome-output PATH    Write OperationOutcome-compatible JSON
--ci-summary-output PATH           Write one-line CI summary
--agent-output                     Single machine-readable JSON object on stdout
--max-issues N                     Limit issues in console/agent output
--fail-fast                        Show only the first issue
--changed-from RUN                 Validate only files changed since a prior evidence run
--log-level LEVEL                  Structured logs to stderr (DEBUG|INFO|WARNING|ERROR)
```

**Examples**

```bash
pyfhircheck file examples/invalid-patient.json --json-output report.json
pyfhircheck bundle examples/bundle.json -c examples/pyfhircheck.json
pyfhircheck server https://hapi.fhir.org/baseR4 -c examples/pyfhircheck.json
pyfhircheck package-fetch -c examples/pyfhircheck.json
pyfhircheck conformance examples/conformance
pyfhircheck compare evidence/run-a evidence/run-b --fail-on-new-errors
pyfhircheck explain datatype.invalid --json
```

## Python library

```python
from pyfhircheck import Validator, ValidationReport
from pyfhircheck.config import ValidatorConfig

config = ValidatorConfig.load("pyfhircheck.json")
validator = Validator(config)

patient = {
    "resourceType": "Patient",
    "id": "example",
    "gender": "female",
}

report: ValidationReport = validator.validate_resource(patient)
print(report.status.value)          # PASS | WARN | FAIL
print(len(report.errors))           # error count
print(report.to_dict()["runId"])    # correlation / evidence id
```

Public exports also include typed exceptions (`ConfigError`, `PackageError`, `EvidenceError`, …) and `ValidationIssue`.

## Configuration

See [examples/pyfhircheck.json](examples/pyfhircheck.json) for a working config.

| Field | Purpose |
|-------|---------|
| `fhirVersion` | FHIR version (`4.0.1` / `R4`) |
| `packages` | NPM package id + version to resolve before validation |
| `packageCacheDir` | Local cache for resolved `.tgz` packages |
| `localPackagePaths` | Local StructureDefinition JSON, folders, or `.tgz` files |
| `remotePackageSources` | Remote `.tgz` package URLs |
| `profiles` | Enforced profile URLs per resource type |
| `terminology.mode` | `off`, `local`, or `strict` |
| `ciFailureThreshold` | Fail CI on `error` (default) or `warning` |
| `customRules` | Project-specific rule settings |
| `evidenceOutputDir` | Where validation evidence is persisted |
| `serverValidationTargets` | Resource types fetched by `server` |

Load config from a dict in code:

```python
config = ValidatorConfig.load_dict({"fhirVersion": "4.0.1", "terminology": {"mode": "local"}})
```

## Evidence and drift

Every validation run writes a directory under `evidence/<run-id>/`:

```
evidence/<run-id>/
├── manifest.json           # run metadata and file index
├── report.json             # full validation report
├── operation-outcome.json  # OperationOutcome-compatible issues
├── ci-summary.txt          # one-line PASS/FAIL summary
├── config.json             # config snapshot used for the run
└── inputs.json             # input file content hashes
```

Compare two runs:

```bash
pyfhircheck compare evidence/run-a evidence/run-b --fail-on-new-errors
```

The diff reports new errors, resolved issues, severity changes, and config/profile/terminology drift.

## CI integration

```bash
pyfhircheck folder fhir-resources -c pyfhircheck.json \
  --json-output validation-report.json \
  --operation-outcome-output operation-outcome.json \
  --ci-summary-output validation-summary.txt
```

Use exit code `1` to fail the pipeline when validation errors are present. Set `"ciFailureThreshold": "warning"` in config if warnings should also fail CI.

> [!TIP]
> Pair `--changed-from` with evidence from a previous run to validate only modified files while keeping unchanged resources available for reference resolution.

## Agent and automation output

For LLM agents and CI parsers, use `--agent-output` to emit a single JSON object (`pyfhircheck.agent-output.v1`) with status, truncated top issues (including rule hints and fingerprints), and the evidence path.

```bash
pyfhircheck file patient.json --agent-output --max-issues 5
pyfhircheck rules   # machine-readable rule catalog
pyfhircheck explain profile.required --json
```

## Observability

Structured JSON logs are written to **stderr** (stdout stays clean for `--agent-output` and piped JSON).

```bash
export PYFHIRCHECK_LOG_LEVEL=INFO      # default: WARNING
export PYFHIRCHECK_LOG_FORMAT=json   # or console

pyfhircheck file patient.json --log-level INFO
```

Logs include correlation IDs, run timing, package download retries, and validation summaries.

## Conformance fixtures

Fixture files assert expected validation outcomes. Minimal case:

```json
{
  "expectedStatus": "PASS",
  "resource": {"resourceType": "Patient", "id": "p1", "gender": "female"}
}
```

Issue-level expectations:

```json
{
  "expectedStatus": "FAIL",
  "expectedIssues": [
    {"severity": "error", "code": "datatype.invalid", "path": "Patient.id"}
  ],
  "resource": {"resourceType": "Patient", "id": "bad id"}
}
```

`expectedOperationOutcome.issue` is also accepted for OperationOutcome-compatible matching. Run fixtures with:

```bash
pyfhircheck conformance examples/conformance
```

## Development

```bash
uv sync
uv run pytest tests/ -v
uv run mypy src/pyfhircheck/
uv run python -m build
```

Copy [docs/github-ci-workflow.yml](docs/github-ci-workflow.yml) to `.github/workflows/ci.yml` to enable GitHub Actions CI.

## HL7 parity

pyfhircheck is tested against the official [HL7 fhir-test-cases](https://github.com/FHIR/fhir-test-cases) conformance suite. Each test case includes a FHIR resource and the expected HAPI validator outcome.

| Metric | Value |
|--------|-------|
| Total R4 JSON cases evaluated | 134 |
| Matches (same pass/fail as HAPI) | 107 |
| False positives (we flag errors, HAPI passes) | 4 |
| False negatives (HAPI flags errors, we pass) | 23 |
| Parity | **79.9%** |

Run the parity suite yourself:

```python
from pathlib import Path
from pyfhircheck.parity.hl7_runner import run_hl7_test_cases, format_hl7_report

report = run_hl7_test_cases(Path("/tmp/fhir-test-cases"), Path(".pyfhircheck/packages"))
print(format_hl7_report(report))
```

Remaining gaps are primarily in display name validation (requires external LOINC/CVX terminology data), advanced Bundle semantics, and StructureDefinition meta-validation.

## Current limitations

> [!WARNING]
> pyfhircheck covers structure, profiles, terminology, extensions, references, invariants, and Bundle validation but does not yet handle: full HL7-compatible snapshot generation for every differential edge case, complete slicing/reslicing semantics, HAPI-identical FHIRPath behavior for all expressions, authenticated private package registries, exhaustive ValueSet expansion against remote terminology servers, or XML/narrative validation.

## Roadmap

See [docs/parity-roadmap.md](docs/parity-roadmap.md) for parity gates and current status against HAPI / HL7 validator behavior.
