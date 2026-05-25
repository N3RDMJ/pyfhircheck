# pyfhircheck

pyfhircheck is a Python FHIR R4 validator package and CLI. The long-term goal is functional parity with the HAPI FHIR / HL7 reference validator, with continuous validation evidence and drift detection inspired by MedVertical Records.

This first version is intentionally smaller than HAPI, but it is not just a JSON schema checker. It performs an end-to-end validation loop for resources, Bundles, folders, and FHIR server search results, then writes structured reports and evidence that can be compared across runs.

## Current Capabilities

- Validate one FHIR JSON resource file, one Bundle, a folder of JSON resources, or resources fetched from a FHIR server.
- Validate base FHIR R4 structure for common clinical resources: Patient, Practitioner, Encounter, Composition, Observation, Condition, Procedure, MedicationRequest, DiagnosticReport, DocumentReference, Organization, and Bundle.
- Check valid JSON, `resourceType`, required fields, cardinality, choice elements, primitive datatype formats, selected complex datatype fields, unknown elements, contained resources, duplicate ids, modifier extensions, extension shape, Bundle entries, document first `Composition`, message first `MessageHeader`, transaction/batch/history request entries, response Bundle status entries, searchset metadata, duplicate `fullUrl`, and reference consistency across contained resources, Bundle `fullUrl`, relative references, absolute references, conditional references, and configured target types.
- Enforce profiles from config and `meta.profile`; includes a built-in example profile and can load simple StructureDefinition JSON snapshots/differentials from local files, folders, `.tgz` FHIR packages, or remote package URLs.
- Resolve configured FHIR package ids/versions into a local cache before validation, and record resolved package evidence in reports.
- Build effective validation snapshots by overlaying differential StructureDefinitions onto loaded base snapshots when `baseDefinition` is available.
- Apply StructureDefinition cardinality, fixed values, pattern values, selected terminology bindings, nested differential element constraints, and FHIRPath invariants through `fhirpathpy` with a small fallback evaluator for minimal/offline environments.
- Validate loaded extension definitions, including extension URL resolution, allowed `value[x]` types, required value cardinality, required nested extension slices, and modifierExtension definitions marked as modifiers.
- Enforce basic sliced profile elements using value, pattern, and exists discriminators for repeated elements.
- Load CodeSystem and ValueSet resources from local/remote packages for local terminology membership checks, including explicit concepts, simple compose includes/excludes, expansion contains, and basic filters over code/display/designation/properties.
- Validate required terminology bindings for selected R4 code elements.
- Run custom project rules that emit the same internal issue model as native validation.
- Produce console, JSON, OperationOutcome-compatible, CI summary, and persisted evidence output.
- Compare two evidence runs for new issues, resolved issues, severity changes, config/profile/terminology changes, and new-error drift.
- Run conformance fixtures that assert final PASS/WARN/FAIL plus expected internal issues or OperationOutcome-compatible expected issues.

## Install

```bash
python3 -m pip install -e ".[dev]"
```

## CLI Examples

```bash
pyfhircheck file examples/valid-patient.json
pyfhircheck file examples/invalid-patient.json --json-output report.json
pyfhircheck bundle examples/bundle.json -c examples/pyfhircheck.json
pyfhircheck folder path/to/fhir-json-folder -c examples/pyfhircheck.json
pyfhircheck server https://hapi.fhir.org/baseR4 -c examples/pyfhircheck.json
pyfhircheck validate-config -c examples/pyfhircheck.json
pyfhircheck package-fetch -c examples/pyfhircheck.json
pyfhircheck conformance examples/conformance
pyfhircheck compare evidence/run-a evidence/run-b --fail-on-new-errors
pyfhircheck export-evidence evidence/run-a exported-evidence
```

Exit codes:

- `0`: validation passed, or warnings did not exceed the configured CI threshold
- `1`: validation failed
- `2`: config, validator, or runtime error

## Config Example

See [examples/pyfhircheck.json](examples/pyfhircheck.json).

Important fields:

- `fhirVersion`: currently `4.0.1`
- `enabledIGs`: IG/package labels included in reports
- `packages`: package ids and versions to resolve into the local cache before validation
- `packageCacheDir`: where resolved `.tgz` packages are stored
- `localPackagePaths`: local StructureDefinition JSON files, folders, or `.tgz` FHIR packages
- `remotePackageSources`: remote `.tgz` package URLs
- `terminology.mode`: `off`, `local`, or `strict`
- `profiles`: enforced profile URLs per resource type
- `ciFailureThreshold`: `error` or `warning`
- `customRules`: project-specific rule settings
- `evidenceOutputDir`: persisted evidence location
- `serverValidationTargets`: resource types fetched by `server`

## Reports And Evidence

Every validation run includes:

- run id
- timestamp
- validator version
- FHIR version
- input source
- resource count
- configured profiles and IG labels
- terminology settings
- error/warning/info counts
- final `PASS`, `WARN`, or `FAIL`
- deterministic hash of inputs and config

Evidence is written under `evidence/<run-id>/` with:

- `report.json`
- `operation-outcome.json`
- `ci-summary.txt`

## Custom Rules

Supported built-in custom rule settings:

- `patientIdentifierSystem`: require `Patient.identifier.system`
- `encounterRequiresPatient`: require `Encounter.subject` to reference a Patient
- `compositionRequiredSections`: require Composition section titles
- `bundleRequiredResourceTypes`: require resource types in a Bundle
- `resolveLocalReferences`: require local references to resolve in the validation set

## CI Usage

```bash
pyfhircheck folder fhir-resources -c pyfhircheck.json --json-output validation-report.json --ci-summary-output validation-summary.txt
```

Use exit code `1` to fail CI when errors are present. Set `ciFailureThreshold` to `warning` if warnings should fail CI too.

## Conformance Fixtures

Conformance cases can assert only final status:

```json
{
  "expectedStatus": "PASS",
  "resource": {"resourceType": "Patient", "id": "p1"}
}
```

They can also assert expected issues:

```json
{
  "expectedStatus": "FAIL",
  "expectedIssues": [
    {"severity": "error", "code": "datatype.invalid", "path": "Patient.id"}
  ],
  "resource": {"resourceType": "Patient", "id": "bad id"}
}
```

`expectedOperationOutcome.issue` is also accepted for OperationOutcome-compatible expected issue matching.

## Current Gaps Versus HAPI / HL7 Validator

This version does not yet provide full HL7-compatible StructureDefinition snapshot generation for every differential edge case, complete slicing/reslicing semantics, complete resource coverage, HAPI-identical FHIRPath edge-case behavior, authenticated/private package registries, full ValueSet expansion semantics for all filter operators/imports/inactive/version handling, all invariants, all extension slicing/profile edge cases, every Bundle/reference edge case, or complete HL7 test-case parity. It is a working foundation with deterministic outputs and evidence, not a replacement for HAPI in regulated production validation.

## Roadmap Toward Fuller Parity

See [docs/parity-roadmap.md](docs/parity-roadmap.md) for the parity gates.

1. Load official HL7 R4 definitions and validate all resources/elements.
2. Implement full snapshot generation and differential merging.
3. Add full FHIRPath invariant evaluation with compiled expression caching.
4. Implement slicing discriminators and extension profile validation.
5. Add NPM package download/cache support for IGs.
6. Add terminology server integration with local-first caching.
7. Build an HL7 `fhir-test-cases` conformance harness.
8. Expand server validation to paging, compartments, and dataset-level consistency checks.
