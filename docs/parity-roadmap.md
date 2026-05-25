# HAPI FHIR Validator Parity Roadmap

pyfhircheck does not yet have HAPI FHIR Validator parity. This roadmap defines the concrete gates needed before that claim can be made.

## Parity Gates

1. Official package coverage
   - Load `hl7.fhir.r4.core` from `.tgz` and remote package URLs.
   - Resolve package ids and versions into a reproducible local cache.
   - Derive resource, complex datatype, cardinality, binding, reference target, and choice element definitions from `StructureDefinition.snapshot.element`.
   - Preserve package identity and loaded definition counts in every evidence report.

2. Profile and IG support
   - Load IG package StructureDefinitions.
   - Merge differential profiles into base snapshots.
   - Enforce profile cardinality, fixed values, pattern values, terminology bindings, invariants, extension rules, and slicing.

3. FHIRPath parity
   - Replace the current lightweight evaluator with a complete FHIRPath implementation.
   - Cache compiled expressions by FHIR version and expression.
   - Match HAPI/HL7 behavior for invariant pass/fail/error handling.

4. Terminology parity
   - Load CodeSystem and ValueSet resources from packages.
   - Expand ValueSets locally where possible.
   - Support remote terminology servers with caching, retries, and deterministic evidence.

5. Reference and Bundle parity
   - Resolve contained, Bundle-local, absolute, conditional, and server references.
   - Complete Bundle-type-specific edge cases beyond the current document, message, transaction, batch, response, searchset, and history checks.

6. HL7 conformance suite
   - Add a reproducible harness for HL7 `fhir-test-cases`.
   - Track pass rate and regressions in CI.
   - Require explicit expected-result fixtures for project-specific cases.

7. Evidence and drift
   - Store package versions, terminology source versions, profile snapshots, validator options, and deterministic hashes.
   - Compare validation issue drift, config drift, package drift, profile drift, and terminology drift.

## Current Status

**91.1% parity** (123/135 matches) against the HL7 fhir-test-cases suite (R4 JSON cases).

| Gate | Status |
|------|--------|
| Official package coverage | Done — `.tgz` loading, remote packages, lazy resource definitions, FHIRPath system type mapping, contentReference handling |
| Profile and IG support | Done — snapshot merge, cardinality, fixed/pattern values, bindings, invariants, slicing (value/pattern/exists/type discriminators), extension definitions, choice[x] binding resolution |
| FHIRPath parity | Partial — `fhirpathpy` backend with LRU caching, hardcoded fallbacks for unsupported expressions (per-1, Period comparison) |
| Terminology parity | Done — package CodeSystem/ValueSet loading, include/exclude/filter expansion, display name validation, unresolved system tracking |
| Reference and Bundle parity | Done — contained, Bundle-local, absolute, conditional, URN references, versioned duplicate detection, searchset validation, document/transaction/batch/history rules |
| HL7 conformance suite | Done — reproducible harness in `pyfhircheck.parity.hl7_runner` |
| Evidence and drift | Done — deterministic hashes, config snapshots, package versions, drift comparison |

### Remaining gaps (12 mismatches)

- **Display name validation** (4 cases) — requires external LOINC/CVX terminology data not bundled in the R4 core package
- **Versioned Bundle reference resolution** (2 cases) — ambiguous references with multiple versioned entries, CHECK_EXISTS_AND_TYPE validation
- **Extension context matching** (1 case) — parent path acceptance for primitive child element contexts
- **XHTML narrative validation** (1 case) — txt-1 constraint and invalid attribute detection
- **Advanced slicing** (1 case) — duplicate slicing definitions on extension elements
- **URN reference resolution** (1 case) — NarrativeLink extension validation and document entry reachability
- **Profile magic codes** (1 case) — vital signs body temperature LOINC code enforcement
- **Package-local terminology** (1 case) — ValueSet membership from custom IG packages
