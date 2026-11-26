# Lumen-ex architecture (Milestone 01)

Lumen-ex is a single-process Python application. It runs locally, offline, on CPU;
there are no services, model downloads, or runtime network dependencies. The root
`main.py` is the evaluator entry point and `lumen_ex` contains the application
foundation. The official starter remains available under `starter/` and is currently
called through a narrow compatibility adapter.

## Data flow

```text
raksha.toml → main.py / CLI → validated policy
                            → starter compatibility adapter (current baseline)

Future processing flow:
input discovery → ingestion → normalization → PII detection → record linkage
                → privacy transformation → leak scan → output writer → report
```

The second flow describes extension points, not implemented privacy functionality.
The current adapter preserves the shipped starter's basic behavior and its known
limitations; it is not a production-ready firewall.

## Boundaries and responsibilities

| Area | Responsibility and expected interface |
|---|---|
| CLI / orchestration | Parse submission arguments, load validated settings, enforce safe path relationships, and invoke the pipeline. `lumen_ex.cli.main` is the executable interface. |
| Input discovery / ingestion | `lumen_ex.ingestion.StreamingIngestor` enumerates files deterministically, hashes them in chunks, and streams CSV/TSV rows, supported JSON record arrays, FHIR Patient resources, and note documents with stable locators. |
| Shared contracts | Hold file metadata, normalized records, typed processing outcomes, linkage decisions, manifest entries, and aggregate reports without depending on format-specific algorithms. |
| Normalization | Normalize representation while retaining source locators and medical values. `Normalizer.normalize` accepts and returns a `NormalizedRecord`. |
| PII detection | Produce counts and typed findings without deciding identity. `Detector.detect` returns `DetectionResult`. No detector is implemented in this milestone. |
| Identity extraction and normalization | `lumen_ex.identity` projects parsed row/object values, FHIR R4 Patient resources, or labeled note fields into a compact `PatientIdentity`. Original values, normalized values, missing/invalid fields, and source locators are retained without copying full records. |
| Identity registry storage | `lumen_ex.registry.EncryptedIdentityRegistry` stores encrypted evidence in one per-run SQLite database and keyed block indexes for candidate retrieval. It is shared across batches and removes its private database directory on close. |
| Indic name normalization | `lumen_ex.detection.indic_names` normalizes supplied Latin/Devanagari name candidates with ITRANS transliteration, Unicode, punctuation, and a small honorific vocabulary. It preserves initials and token order; name equality is not proof of identity. |
| Candidate matching | `lumen_ex.matching.IdentityMatcher` retrieves a bounded set of exact indexed candidates and scores corroborating evidence. Names alone never link; ambiguous or weak evidence stays unresolved, and same-namespace hospital-ID conflicts block links. The matcher returns a decision only; it does not persist mappings or merge patients. |
| Privacy transformation | Transform a normalized record using detection and linkage outputs. `PrivacyTransformer.transform` returns `SanitizationResult`. No anonymization is implemented. |
| Leak scanning / security | Check transformed results before release; unsafe results must not reach output. `LeakScanner.is_safe` is fail-closed in the coordinator. |
| Output writing | Serialize approved results while preserving the official output contract. `OutputWriter` is the format-specific boundary. |
| Reporting | Aggregate safe counts, hashes, durations, and resource metrics. `Reporter` must not include source names, record values, or exception text. |
| Validation / testing | Unit tests exercise contracts and coordination; official validators remain outside the application and are not modified. |

Pipeline stages depend on the shared contracts rather than one another's internal
implementations. The coordinator processes records in order, sends only leak-scanned
sanitized results to the writer, reports every result, and then finalizes the report.
Its stage dependencies are injected, so later milestones can add implementations
without changing the contracts or creating microservices.

The identity helpers consume parsed values from the ingestion boundary. File reading is
streamed, but the current JSON record-array discovery is intentionally limited to common
collection keys and whole-document JSON values are materialized as one record. FHIR
validation remains an export responsibility. Free-text extraction is limited to
explicit field labels; it is not a general name recognizer and does not perform record
linking. The reader is not yet wired into the production CLI.

The registry provides encrypted storage, exact keyed block lookup, and streaming
candidate iteration. Candidate scoring is a separate stage and does not merge patients.
Global cluster reconciliation remains a later stage. The matcher uses a bounded
candidate set; overflow is unresolved rather than truncated to a potentially misleading
best match. Its database is per-run and must be closed to remove the encrypted database
and SQLite sidecars.

## Operational constraints

The command requires `RAKSHA_SECRET` and does not print or persist it. Logs are limited
to safe error codes and aggregate starter counts. Processing errors carry codes rather
than source text. Unsupported or malformed inputs continue to be quarantined by the
existing starter. These are engineering choices, not a claim that the current baseline
removes PII reliably or provides a complete security guarantee.
