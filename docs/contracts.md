# Shared contracts (foundation through detection)

Internal Python dataclasses and enums live in `lumen_ex.contracts.models`. They are
deliberately separate from on-disk submission formats so pipeline code can evolve
without changing the evaluator interface.

## Internal records

- `InputFileMetadata`: relative source path (hidden from its repr), SHA-256, byte size,
  media type, detected format, and encoding. `source_id` is a deterministic digest of
  the relative path and input hash.
- `ParsedRecord`: one parsed record unit with a source locator and hidden values.
  `structure` can preserve source shape such as original CSV headers and cells; its
  `record_id` is derived from the source digest and locator.
- `IngestionFileResult`, `IngestionStatus`, and `IngestionStatistics`: per-file success
  or quarantine with a safe error code, record counts, and aggregate file/byte totals.
- `IngestionResult`: ordered file outcomes and a lazy iterator over records from files
  that passed prevalidation. Callers must inspect file outcomes rather than assuming
  every discovered file was ingested.
- `NormalizedRecord`: source metadata, a source locator, parsed fields, optional identity
  projections, and optional source structure. Field values, locators, and projections
  are excluded from repr to reduce accidental logging exposure.
- `DetectionResult`: non-negative detection totals, entity-type counts, and optional
  findings. A `DetectionFinding` carries an entity type, source channel, source key, and
  character span; the raw matched value and source key are excluded from repr.
- `LinkageAction` / `LinkageDecision`: LINK requires a patient reference; DO_NOT_LINK and
  ABSTAIN do not. Patient references are excluded from repr.
- `ProcessingStatus`: sanitized, quarantined, or suppressed.
- `SanitizationResult`: output locator/fields, safe error code if quarantined, and
  non-negative metrics. M06 adds output headers/path, an optional keyed patient
  pseudonym, and an aggregate `PrivacyRiskAssessment`; raw values remain hidden from
  repr.
- `PrivacyRiskAssessment`: cohort counts, equivalence-class sizes, estimated mean
  re-identification risk, rare-value count, and suppressed-attribute names. It does not
  retain raw quasi-identifier values.
- `OutputManifestEntry`: source hash, optional relative output, status, and a safe
  quarantine reason code.
- `PipelineReport`: manifest entries, aggregate counts, timing, resource use, and
  optional entity counts.

`ProcessingError` carries a safe code, not source content. The coordinator converts
expected processing errors, unexpected stage failures, and leak-scan failures into
quarantine results. Errors at output/report boundaries become a safe `internal_error`
or `leak_scan_failed`, never raw exception text.

## Identity projection

`PatientIdentity` in `lumen_ex.contracts.identity` contains only identity attributes
and their `IdentityValue(original, normalized, source_key)` entries. Supported
attributes are patient and relative names, date of birth, age, gender, phone, email,
address, location, hospital-specific identifier, government/health identifier, and
insurance identifier.
It also records the source file and record locator, missing and malformed fields, and
normalization metadata. Source values and locators are excluded from object
representations to reduce accidental logging exposure. The projection deliberately does
not retain the complete medical record.

`lumen_ex.identity.extract_identity` handles one parsed mapping or a labeled note;
`extract_fhir_identities` returns identities for Patient resources in a FHIR R4
resource or Bundle. `PatientIdentityNormalizer` attaches those projections to
`NormalizedRecord` while retaining the parsed source fields and structure. These
helpers do not decode files or guess unlabeled names. Relative names are retained only
when explicitly labeled, nested under a relative context, or attached to a patient-name
value using an S/o, D/o, W/o, or C/o marker. Email values must pass a bounded address
shape check before they are used for matching.

## Linkage decisions

`IdentityMatcher.evaluate` returns an internal `IdentityMatchResult` with deterministic
candidate scores and a bounded evidence list. It supports a unique strong identifier
or a unique name plus DOB, relative name, or village and age within one year; name-only
and ambiguous matches remain unresolved. `IdentityLinker` maps a linked identity to
`LinkageAction.LINK`, a safely new identity to `DO_NOT_LINK`, and uncertain or
multi-identity input to `ABSTAIN`. Registry cluster merges reject transitive conflicts
between different hospital identifiers in the same hospital namespace. These scores
are evidence rankings, not calibrated probabilities.

## Stage interfaces

Protocols in `lumen_ex.stages` define the boundaries for ingestion, normalization,
detection, linkage, privacy transformation, leak scanning, writing, and reporting.
These are interface contracts; ingestion, identity normalization, deterministic PII
detection, linkage, and privacy transformation have modular implementations.
`MultilingualPIIDetector` returns findings for structured values,
free-text mentions, JSON keys, CSV headers, and filenames without modifying the record.
The production starter adapter reuses it through `SensitiveDataLeakScanner` before
writing sanitized outputs and metadata. The M06 `PrivacyTransformer` exposes a singleton stage adapter
and a cohort-aware `transform_batch` API; the record-at-a-time coordinator does not
yet provide the cohort integration needed to use it for release. The coordinator exposes `Pipeline.ingest_directory`
and receives normalization, detection, linkage, transformation, leak-scanning,
output-writing, and reporting implementations through `PipelineStages`. It writes only
a result marked sanitized after the leak scanner accepts it, records every status for
reporting, and finalizes a manifest after processing.

## Official output compatibility

The governing format is the starter kit's `docs/SPEC.md`, not these internal models:

- `report.json` retains schema `raksha-report/1`. `PipelineReport.to_official_dict()`
  emits the specified file, count, timing, and resource keys. File entries use
  `input_sha256`, `output`, and `status`, adding `reason` only for quarantine.
- `records.jsonl` remains one object per exported record with the existing required
  `file`, `loc`, and `patient` keys. No new required fields are introduced.
- Output paths remain relative to the output root; hashes identify inputs without
  exposing source filenames in the report.
- `PatientIdentity` and its `NormalizedRecord` projections are internal contracts only.
  They do not change `records.jsonl`, the report schema, or any pseudonym behavior.

The existing starter is still responsible for baseline serialization. The M07 scanner
does not alter the validator or official output shapes; see [security boundaries and
limitations](security.md).
