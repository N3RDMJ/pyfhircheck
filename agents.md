# pyfhircheck

A fast, comprehensive FHIR resource validator for Python — targeting HAPI-level coverage with Python-native performance.

## Project overview

pyfhircheck validates FHIR R4/R5 resources against StructureDefinitions, ValueSets, CodeSystems, and Implementation Guide profiles. The goal is to be the first Python FHIR validator that matches the depth of HAPI (Java) and medvertical/records-fhir-validator (TypeScript) while being significantly faster through async I/O, compiled FHIRPath caching, and zero-copy parsing.

## Architecture

### Executor pipeline

Validation runs through an ordered pipeline of independent executors, each responsible for one validation aspect. Every executor is independently toggleable and produces typed `ValidationIssue` results with severity (error/warning/information).

```
StructuralExecutor → ProfileExecutor → TerminologyExecutor →
InvariantExecutor → ReferenceExecutor → MetadataExecutor →
CustomRuleExecutor
```

### Core modules

| Module | Responsibility |
|--------|---------------|
| `pyfhircheck.core.engine` | Orchestrates the executor pipeline, collects issues |
| `pyfhircheck.core.executors.*` | One module per executor (structural, profile, terminology, invariant, reference, metadata, custom) |
| `pyfhircheck.profiles.loader` | Loads StructureDefinitions from FHIR NPM packages (.tgz) and local files |
| `pyfhircheck.profiles.snapshot` | Generates snapshots from differential-only StructureDefinitions |
| `pyfhircheck.terminology.resolver` | Stratified terminology resolution (local-first / server-first / local-only) with circuit breaker |
| `pyfhircheck.terminology.valueset` | ValueSet expansion and membership testing |
| `pyfhircheck.fhirpath.evaluator` | FHIRPath expression compilation, caching (LRU, keyed by version+expression), and evaluation |
| `pyfhircheck.slicing` | Discriminator matching (value, pattern, type, profile, exists), slice cardinality enforcement |
| `pyfhircheck.cache` | Multi-layer cache: FHIRPath expressions, SD snapshots, ValueSet expansions, terminology lookups |
| `pyfhircheck.cli` | CLI entry point (`python -m pyfhircheck`) |

### Performance strategy

- Async I/O for remote terminology server calls and NPM package downloads
- LRU caches at four levels: FHIRPath expressions, SD snapshots, ValueSet expansions, code lookups
- orjson for JSON parsing
- Lazy loading of StructureDefinitions (only load what the resource actually references)
- Profile-parallel validation when multiple profiles declared in `meta.profile`

## Commands

```bash
# Install (editable)
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run tests with coverage
python -m pytest tests/ --cov=pyfhircheck --cov-report=term-missing

# Type check
mypy src/pyfhircheck/

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Run the validator CLI
python -m pyfhircheck validate <resource.json>

# Run conformance test suite against HL7 fhir-test-cases
python -m pytest tests/conformance/ -v --tb=short
```

## Code standards

- Python 3.11+ required
- Type hints on all public functions and methods — use `typing` and `collections.abc`
- Use `dataclasses` or `attrs` for data structures, not raw dicts
- Async functions for anything that touches network (terminology servers, package downloads)
- Validation issues use the `ValidationIssue` dataclass with fields: `severity`, `path`, `message`, `rule_id`, `details`
- Element paths use FHIR dot notation: `Patient.name[0].given[0]`
- Use `enum.Enum` for fixed vocabularies (severity levels, binding strengths, discriminator types)
- No wildcard imports
- Prefer `match` statements over if/elif chains for discriminated types
- Tests go in `tests/` mirroring `src/` structure. Use pytest fixtures, not unittest classes

## FHIR validation dimensions

Every executor must handle these validation aspects:

| Aspect | What to check |
|--------|--------------|
| **Structure** | Every element described by the StructureDefinition; no unknown elements |
| **Cardinality** | min/max from element definitions |
| **Data types** | Values conform to declared types and regex/format rules |
| **Terminology** | Codes in bound ValueSets; severity by binding strength (required=error, extensible=warning, preferred=info, example=skip) |
| **Invariants** | FHIRPath `constraint` entries evaluate to true |
| **Profiles** | All profiles in `meta.profile` loaded and enforced |
| **Slicing** | Discriminator resolution, per-slice cardinality, open vs closed |
| **Extensions** | URL resolves to known SD; value types, cardinality, nested structure valid |
| **References** | Format, type constraints, bundle-internal/contained/external resolvability |
| **Questionnaire** | QuestionnaireResponse validates against paired Questionnaire |
| **Snapshots** | Differential-only profiles get snapshots generated before validation |

## Key dependencies

- `fhirpathpy` — FHIRPath expression evaluation
- `orjson` — Fast JSON parsing
- `httpx` — Async HTTP client for terminology servers and package registry
- `pydantic` (optional) — For CLI config and settings validation only, not for FHIR models
- `click` or `typer` — CLI framework
- `pytest`, `pytest-asyncio` — Testing

## Testing strategy

### Conformance suite
The primary correctness measure is pass rate against the [HL7 fhir-test-cases](https://github.com/FHIR/fhir-test-cases) repository. Each test case includes a FHIR resource and expected OperationOutcome. Build this harness early.

### Unit tests
Each executor gets its own test module with handcrafted resources covering:
- Happy path (valid resources pass)
- Each specific validation rule (invalid resources produce the correct issue)
- Edge cases (empty arrays, missing optional fields, deeply nested structures)

### Integration tests
Full pipeline tests with real-world IG profiles (US Core, IPS, mCODE) validating complete resources.

## Reference implementations

When designing a module, consult these for patterns:
- **HAPI FHIR** — ValidationSupportChain, Instance Validator, modular validation support modules
- **medvertical/records-fhir-validator** — TypeScript executor pipeline, 496/496 HL7 test-cases pass rate, FHIRPath caching, terminology circuit breaker

## Agents

Specialized agents are defined in `.claude/agents/`. Use them via `--agent <name>` or `@agent-<name>` in conversation:

- `fhir-spec` — FHIR specification expert for conformance questions and spec interpretation
- `validator-arch` — Architecture decisions, executor design, module boundaries
- `test-harness` — HL7 fhir-test-cases conformance suite management
- `terminology` — Terminology binding, ValueSet expansion, CodeSystem validation
- `perf` — Profiling, caching, async optimization, benchmarking
