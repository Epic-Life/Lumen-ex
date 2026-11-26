# Shared contracts (Milestone 01)

Internal Python dataclasses and enums live in `lumen_ex.contracts.models`. They are
deliberately separate from on-disk submission formats so pipeline code can evolve
without changing the evaluator interface.

## Internal records

- `InputFileMetadata`: relative source path (hidden from its repr), SHA-256, byte size,
  and optional media type.
- `ParsedRecord`: one decoded/parsed record unit, with values hidden from repr until a
  normalizer produces the downstream representation.
- `NormalizedRecord`: source metadata, a source locator, and parsed fields. Field values
  are excluded from repr to reduce accidental logging exposure.
- `DetectionResult`: non-negative detection totals and optional entity-type counts.
- `LinkageAction` / `LinkageDecision`: LINK requires a patient reference; DO_NOT_LINK and
  ABSTAIN do not. Patient references are excluded from repr.
- `ProcessingStatus`: sanitized, quarantined, or suppressed.
- `SanitizationResult`: output locator/fields, safe error code if quarantined, and
  non-negative metrics.
- `OutputManifestEntry`: source hash, optional relative output, status, and a safe
  quarantine reason code.
- `PipelineReport`: manifest entries, aggregate counts, timing, resource use, and
  optional entity counts.

`ProcessingError` carries a safe code, not source content. The coordinator converts
expected processing errors and leak-scan failures into quarantine results. Unexpected
programming errors are not silently presented as successful processing.

## Stage interfaces

Protocols in `lumen_ex.stages` define the boundaries for ingestion, normalization,
detection, linkage, privacy transformation, leak scanning, writing, and reporting.
these are interface contracts only; the algorithms and I/O implementations are later
milestones. The minimal coordinator receives normalization, detection, linkage,
transformation, leak-scanning, output-writing, and reporting implementations through
`PipelineStages`. It writes only a result marked sanitized after the leak scanner accepts
it, records every status for reporting, and finalizes a manifest after processing.

## Official output compatibility

The governing format is the starter kit's `docs/SPEC.md`, not these internal models:

- `report.json` retains schema `raksha-report/1`. `PipelineReport.to_official_dict()`
  emits the specified file, count, timing, and resource keys. File entries use
  `input_sha256`, `output`, and `status`, adding `reason` only for quarantine.
- `records.jsonl` remains one object per exported record with the existing required
  `file`, `loc`, and `patient` keys. No new required fields are introduced.
- Output paths remain relative to the output root; hashes identify inputs without
  exposing source filenames in the report.

The existing starter is still responsible for actual baseline serialization in this
milestone. The new report serializer is covered as a contract helper and does not alter
the validator or claim that the future exporter is implemented.
