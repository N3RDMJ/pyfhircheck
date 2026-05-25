# pyfhircheck Implementation Plan

## Current State

The project has a working validation engine with:
- Executor-style pipeline (structural, profile, terminology, reference, bundle, extension, custom rules)
- Profile loading from local paths, `.tgz` packages, and remote sources
- Differential-to-snapshot merging for profiles
- FHIRPath invariant evaluation via `fhirpathpy` with regex fallback
- Slicing enforcement (value, pattern, exists discriminators)
- CodeSystem/ValueSet loading and simple expansion
- Evidence storage, drift comparison, and conformance case harness
- CLI with file/folder/bundle/server/compare/conformance commands
- 30+ tests covering all major validation dimensions

What it does **not** yet do:
- Load real-world FHIR NPM packages from the registry (e.g. `hl7.fhir.r4.core`, `de.gematik.isik-basismodul`)
- Handle the full R4 resource type set (only ~14 hardcoded resource definitions)
- Complete slicing semantics (type/profile discriminators, reslicing, closed slicing)
- Full FHIRPath coverage for complex invariants
- Remote terminology server integration
- Questionnaire/QuestionnaireResponse validation
- XML support
- FHIR R5 support

---

## First Goal: Validate ISiK Resources

The first milestone is validating resources against **ISiK Basismodul** (gematik) profiles. This is a concrete, bounded target that forces us to solve every gap that matters for real-world IG validation.

### What ISiK Basismodul Requires

ISiK Stage 3 Basismodul (`de.gematik.isik-basismodul#3.1.1`) profiles cover:
- **ISiKPatient** — Patient with KVNR identifier slicing, identifier type constraints
- **ISiKKontakt** — Encounter with billing-case grouping
- **ISiKDiagnose** — Condition with ICD-10-GM coding
- **ISiKProzedur** — Procedure with OPS coding
- **ISiKOrganisation** — Organization
- **ISiKPersonImGesundheitsberuf** — Practitioner/PractitionerRole

Dependencies:
- `hl7.fhir.r4.core#4.0.1` — base R4 definitions (all 145+ resource types, all complex types)
- `de.basisprofil.r4#1.4.0` — German base profiles
- `hl7.terminology.r4` — terminology codes

ISiK-specific characteristics:
- Heavy use of identifier slicing with `value` discriminators on `system`
- Relaxed terminology validation (SNOMED CT, ICD-10-GM, ATC, OPS excluded from expansion)
- Unknown extensions explicitly allowed
- `errorOnUnknownProfile: false`

### What We Must Build

Each phase below is ordered by dependency — later phases depend on earlier ones.

---

## Phase 1: Full R4 Core Definition Loading

**Goal:** Replace the hardcoded `R4_RESOURCES` dict with definitions loaded from `hl7.fhir.r4.core#4.0.1`.

### 1.1 NPM Package Registry Client
- Implement proper FHIR NPM package resolution from `https://packages.fhir.org`
- Handle the registry's manifest format: `GET /{package}/{version}` returns a `.tgz` URL
- Handle version resolution: `latest` and semver-like versions
- Respect the existing `PackageResolver` cache dir pattern
- Handle transitive dependencies: read `package.json` inside `.tgz` to discover `dependencies`

**Files:** `src/pyfhircheck/profiles/package.py`

### 1.2 Full Resource/ComplexType Definition Generation
- Load all ~145 resource StructureDefinitions from `hl7.fhir.r4.core`
- Load all ~40 complex type StructureDefinitions (currently only 11 hardcoded)
- Handle `BackboneElement` children properly — currently we skip all sub-elements
- Handle choice types `[x]` fully including all R4 type combinations
- Remove the hardcoded `R4_RESOURCES` and `COMPLEX_TYPE_FIELDS` dicts as primary source; keep them as fallback when no package is loaded

**Files:** `src/pyfhircheck/core/definitions.py`, `src/pyfhircheck/profiles/specification.py`

### 1.3 BackboneElement Recursive Validation
- The engine currently treats BackboneElement fields as opaque dicts
- Must recursively validate sub-elements (e.g., `Patient.contact.name`, `Bundle.entry.request.method`)
- Build a nested element tree from StructureDefinition paths
- Validate cardinality, types, bindings at every nesting level

**Files:** `src/pyfhircheck/core/engine.py`, `src/pyfhircheck/core/definitions.py`

### Tests
- Validate a Patient with all common sub-elements against package-loaded definitions
- Validate an Encounter with nested `hospitalization`, `participant` sub-elements
- Ensure no regression on existing 30+ tests

---

## Phase 2: Dependency Resolution and IG Package Loading

**Goal:** Load ISiK Basismodul and its full dependency chain.

### 2.1 Transitive Dependency Resolution
- Parse `package.json` from `.tgz` archives for `dependencies` field
- Build a dependency graph and resolve in topological order
- Cache resolved packages to avoid re-downloading

**Files:** `src/pyfhircheck/profiles/package.py`

### 2.2 IG Profile Loading Pipeline
- Load StructureDefinitions from IG packages after loading their base dependencies
- Ensure snapshot generation has access to base StructureDefinitions when merging differentials
- Handle IG-specific resources: SearchParameter, CapabilityStatement (skip gracefully)
- Handle NamingSystem, OperationDefinition, etc. without crashing

**Files:** `src/pyfhircheck/profiles/loader.py`, `src/pyfhircheck/profiles/snapshot.py`

### 2.3 Config: Package-Based IG Declaration
- Allow declaring ISiK as a configured package:
  ```json
  {
    "packages": [
      {"name": "de.gematik.isik-basismodul", "version": "3.1.1"}
    ]
  }
  ```
- Auto-resolve `de.basisprofil.r4#1.4.0` and `hl7.fhir.r4.core#4.0.1` as transitive dependencies
- Load all StructureDefinitions from all resolved packages

**Files:** `src/pyfhircheck/config.py`, `src/pyfhircheck/core/engine.py`

### Tests
- Integration test: configure ISiK Basismodul, validate a minimal valid ISiKPatient
- Integration test: validate an invalid ISiKPatient (missing KVNR identifier)
- Unit test: dependency resolution produces correct topological order

---

## Phase 3: Snapshot Generation Improvements

**Goal:** Handle differential-only ISiK profiles that derive from German base profiles that themselves derive from R4 base.

### 3.1 Multi-Level Snapshot Resolution
- ISiK profiles often have: ISiKPatient -> de.basisprofil.Patient -> hl7.fhir.r4.core/Patient
- Current `SnapshotResolver` only looks one level up
- Implement recursive base resolution following `baseDefinition` chain
- Handle circular references gracefully

**Files:** `src/pyfhircheck/profiles/snapshot.py`

### 3.2 Correct Element Merging
- FHIR snapshot generation has specific merge rules:
  - Child elements inherit parent constraints
  - `min` can only be raised, `max` can only be lowered
  - Bindings can only be tightened (required > extensible > preferred > example)
  - Types can only be narrowed
- Implement these merge semantics correctly

**Files:** `src/pyfhircheck/profiles/snapshot.py`

### 3.3 Slicing Definition Propagation
- ISiK profiles define slicing on `identifier` with discriminator `type` = `value`, `path` = `system`
- Slice definitions must be propagated from differential to merged snapshot
- Ensure slice-specific element constraints (e.g., `identifier:VersichertenId-GKV.system`) are preserved

**Files:** `src/pyfhircheck/profiles/snapshot.py`, `src/pyfhircheck/profiles/loader.py`

### Tests
- Unit test: three-level snapshot merge produces correct element list
- Unit test: binding tightening rules work correctly
- Integration test: ISiKPatient snapshot contains all expected elements including KVNR slice

---

## Phase 4: Advanced Slicing

**Goal:** Handle ISiK's identifier slicing patterns correctly.

### 4.1 Type Discriminator
- ISiK uses `type` discriminators (discriminator path points to a type, not a value)
- Implement type-based slice matching

### 4.2 Pattern Discriminator on Nested Elements
- ISiK slices `identifier` by `type.coding.system` + `type.coding.code`
- Discriminator path traverses into nested complex types
- Implement deep path traversal for discriminator matching

### 4.3 Closed vs Open Slicing
- Handle `slicing.rules` = `open` | `closed` | `openAtEnd`
- `open` (ISiK default): unmatched elements allowed
- `closed`: unmatched elements produce errors

### 4.4 Slice-Level Element Constraints
- Each slice can have its own required elements, cardinality, bindings
- E.g., `identifier:VersichertenId-GKV.value` has `min: 1`
- Validate slice-matched elements against slice-specific constraints

**Files:** `src/pyfhircheck/core/engine.py`, `src/pyfhircheck/profiles/loader.py`

### Tests
- ISiKPatient with correct KVNR identifier passes
- ISiKPatient without KVNR fails with `profile.slice.cardinality.min`
- ISiKPatient with KVNR but missing `value` fails
- Closed slicing rejects unmatched elements

---

## Phase 5: Terminology Handling for ISiK

**Goal:** Correctly handle ISiK's terminology approach — validate what's available, skip what's not.

### 5.1 Terminology Exclusion List
- ISiK explicitly excludes SNOMED CT, ICD-10-GM, ATC, OPS, LOINC from terminology validation
- Add config support for `ignoredCodeSystems` and `ignoredValueSets`
- When a binding references an ignored CodeSystem/ValueSet, skip validation (return `None`)

```json
{
  "terminology": {
    "mode": "local",
    "ignoredCodeSystems": [
      "http://snomed.info/sct",
      "http://fhir.de/CodeSystem/bfarm/icd-10-gm",
      "http://fhir.de/CodeSystem/bfarm/atc",
      "http://fhir.de/CodeSystem/bfarm/ops"
    ]
  }
}
```

### 5.2 Package-Loaded Terminology
- Load CodeSystem and ValueSet resources from all resolved packages
- Use them for validation of non-excluded code systems
- Handle ValueSets that reference CodeSystems by URL

### 5.3 Terminology Evidence
- Report which CodeSystems/ValueSets were loaded
- Report which were excluded
- Report which lookups returned `None` (unknown) vs `True`/`False`

**Files:** `src/pyfhircheck/terminology/resolver.py`, `src/pyfhircheck/config.py`

### Tests
- Patient with `gender: "female"` passes (known ValueSet)
- Condition with ICD-10-GM code passes when ICD-10-GM is in ignored list
- Condition with ICD-10-GM code produces warning when ICD-10-GM is not loaded and not ignored

---

## Phase 6: ISiK Test Fixtures and Validation Suite

**Goal:** Create a validation test suite using real ISiK resources.

### 6.1 Fetch ISiK Test Resources
- Download test fixtures from `gematik/app-referencevalidator-plugins`
- Extract from `valmodule-isik3-basismodul/src/main/resources/plugin/test-files/`
- Store in `tests/fixtures/isik3-basismodul/`

### 6.2 ISiK Conformance Cases
- Create conformance case files for each ISiK test resource
- Expected PASS for valid resources, expected FAIL for invalid resources
- Run via the existing `conformance` harness

### 6.3 ISiK Config Preset
- Create `examples/isik3-basismodul.json` config file
- Pre-configure ISiK package, terminology exclusions, and profile enforcement

**Files:** `tests/fixtures/isik3-basismodul/`, `examples/isik3-basismodul.json`

### Tests
- Conformance suite pass rate against ISiK test fixtures
- Track pass rate progression as we fix issues

---

## Phase 7: Extension Handling Improvements

**Goal:** Handle ISiK's extension patterns correctly.

### 7.1 Extension Definition Loading from Packages
- Load all Extension StructureDefinitions from IG packages
- Store in `ProfileRegistry._extensions`
- Handle complex extensions with nested extension slices

### 7.2 Unknown Extension Tolerance
- ISiK allows unknown extensions (`allowUnknownExtensions: true`)
- Add config option to suppress `extension.unknown` warnings
- Default to warning (current behavior)

### 7.3 Extension URL Validation
- Validate that extension URLs are well-formed URIs
- When extension definition is loaded, validate value type, cardinality, nested structure

**Files:** `src/pyfhircheck/core/engine.py`, `src/pyfhircheck/profiles/loader.py`, `src/pyfhircheck/config.py`

---

## Phase 8: Deep Element Validation

**Goal:** Validate nested elements and complex type constraints fully.

### 8.1 Nested Element Path Validation
- ISiK profiles constrain paths like `Patient.identifier.type.coding.system`
- Current engine only validates top-level fields
- Implement recursive element constraint checking at any depth

### 8.2 Complex Type Sub-Element Validation
- Validate `Coding.system` is a URI, `Coding.code` is a code, etc.
- Validate `Identifier.type` is a CodeableConcept with correct structure
- Validate `HumanName.given` is an array of strings

### 8.3 Primitive Type Validation Completeness
- Add missing primitive validations: `base64Binary`, `oid`, `uuid`, `positiveInt`, `unsignedInt`, `markdown`, `time`, `xhtml`
- Add regex patterns for `oid` (`urn:oid:...`), `uuid` (`urn:uuid:...`)
- Validate `decimal` precision/range

**Files:** `src/pyfhircheck/core/engine.py`, `src/pyfhircheck/core/definitions.py`

---

## Phase 9: Reporting and Evidence Improvements

**Goal:** Match the reporting depth needed for ISiK validation certification.

### 9.1 Per-Resource Reporting
- Current report aggregates all issues across all resources
- Add per-resource issue grouping in JSON output
- Show which profile(s) each resource was validated against

### 9.2 OperationOutcome Improvements
- Include `location` (XPath) alongside `expression` (FHIRPath)
- Include profile URL in issue details
- Map internal issue codes to FHIR `IssueType` codes more precisely

### 9.3 Validation Summary
- Total resources validated
- Resources by type
- Resources by profile
- Pass/warn/fail counts by resource type
- Package versions used

**Files:** `src/pyfhircheck/reporting/output.py`, `src/pyfhircheck/models.py`

---

## Phase 10: Performance and Robustness

**Goal:** Handle real-world package sizes without degrading performance.

### 10.1 Lazy Loading
- `hl7.fhir.r4.core` contains ~600 StructureDefinitions
- Don't load all into memory at startup
- Index by URL/type, load on first access

### 10.2 Caching
- Cache expanded ValueSets
- Cache resolved snapshots
- Cache compiled FHIRPath expressions (already done via `lru_cache`)

### 10.3 Error Resilience
- Handle malformed StructureDefinitions without crashing
- Handle network failures during package download with retry
- Handle very large bundles without memory exhaustion

**Files:** `src/pyfhircheck/profiles/package.py`, `src/pyfhircheck/core/engine.py`

---

## Implementation Order and Dependencies

```
Phase 1 (R4 Core Loading)
    |
    v
Phase 2 (IG Package Loading)
    |
    v
Phase 3 (Snapshot Generation) <--- Phase 4 (Advanced Slicing)
    |                                       |
    v                                       v
Phase 5 (Terminology)              Phase 7 (Extensions)
    |                                       |
    +--------->  Phase 6 (ISiK Tests) <-----+
                        |
                        v
              Phase 8 (Deep Validation)
                        |
                        v
              Phase 9 (Reporting)
                        |
                        v
              Phase 10 (Performance)
```

Phases 1-5 are sequential and blocking. Phases 4 and 7 can be developed in parallel after Phase 3. Phase 6 starts as soon as packages can be loaded (Phase 2) and grows as validation depth increases.

---

## ISiK Validation Success Criteria

The first goal is achieved when:

1. `pip install -e ".[dev]"` works
2. Running:
   ```bash
   pyfhircheck file patient.json -c isik3-basismodul.json
   ```
   against a valid ISiKPatient returns exit code 0 (PASS)

3. Running against an invalid ISiKPatient (missing KVNR, wrong identifier system) returns exit code 1 (FAIL) with meaningful issue messages

4. The conformance suite pass rate against ISiK Basismodul test fixtures is tracked and improving

5. Evidence reports include ISiK package version, loaded profiles, and terminology configuration

6. The validator correctly handles:
   - ISiKPatient identifier slicing (KVNR)
   - Required elements from ISiK profiles
   - Terminology bindings for known ValueSets
   - Terminology exclusions for SNOMED/ICD-10-GM
   - Unknown extension tolerance
   - German base profile inheritance chain

---

## Beyond ISiK: Road to HAPI Parity

After achieving ISiK validation, the path to HAPI parity requires:

1. **HL7 fhir-test-cases conformance suite** — download, parse, and run the official test cases; track pass rate
2. **Full FHIRPath engine** — replace the lightweight fallback with a complete implementation or improve `fhirpathpy` integration
3. **Remote terminology server** — TX server protocol, caching, circuit breaker
4. **Questionnaire validation** — QuestionnaireResponse against Questionnaire
5. **XML support** — parser + serializer alongside JSON
6. **R5 support** — version-aware definition loading
7. **Continuous validation** — scheduled server validation runs with drift tracking
8. **Additional IG support** — US Core, IPS, mCODE, other gematik modules (Terminplanung, Medikation, Vitalparameter, Dokumentenaustausch)
