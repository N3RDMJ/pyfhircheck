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

The project now has a package-derived definition path, `.tgz` loading, remote package URL loading, configured package id/version resolution into a local cache, resolved package evidence in reports, base snapshot plus differential overlay for effective StructureDefinition elements, differential profile constraint enforcement for top-level and nested element paths, root-level StructureDefinition invariant retention, cached `fhirpathpy` invariant evaluation with fallback behavior, loaded extension definition checks for URL/value[x]/nested slices/modifierExtension safety, basic slicing enforcement for value/pattern/exists discriminators on repeated elements, reference checks for contained resources, Bundle `fullUrl`, relative references, absolute references, conditional references, and target types, Bundle type checks for document/message/transaction/batch/response/searchset/history workflows, package CodeSystem/ValueSet loading with simple include/exclude/filter expansion, a conformance harness for expected PASS/WARN/FAIL fixtures plus issue-level/OperationOutcome-style expectations, evidence output, and drift comparison.

Remaining parity gaps are full HL7-compatible snapshot generation for every differential edge case, complete slicing semantics including type/profile discriminators and reslicing, HAPI-identical FHIRPath edge-case behavior, complete terminology expansion handling for all filter operators/imports/inactive/version semantics, full extension slicing/profile semantics, full Bundle/reference edge-case rules, and measured HL7 test-case pass rate.
