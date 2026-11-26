# Lumen-ex architecture (through privacy transformation)

Lumen-ex is a single-process Python application. It runs locally, offline, on CPU;
there are no services, model downloads, or runtime network dependencies. The root
`main.py` is the evaluator entry point and `lumen_ex` contains the application
foundation. The official starter remains available under `starter/` and is currently
called through a narrow compatibility adapter that injects the configured policy,
per-run secret, and leak scanner.

## Data flow

```text
raksha.toml → main.py / CLI → validated policy and environment secret
                            → starter adapter → in-memory transformation
                                               → leak scan → output files

Modular processing flow:
Pipeline.ingest_directory → per-file ingestion outcomes → parsed records
Parsed records → normalization → PII detection → record linkage
                → cohort privacy transformation → leak scan → output writer → report
```

PII detection, record linkage, and the M06 privacy transformation have modular
implementations. The M07 scanner is wired into the starter adapter before sanitized
files, `records.jsonl`, and `report.json` are written. The M06 cohort transformer is not
wired into production output. The starter still has incomplete PII detection; scanning
is a fail-closed safeguard, not a guarantee of complete de-identification.

## Boundaries and responsibilities

| Area | Responsibility and expected interface |
|---|---|
| CLI / orchestration | Parse submission arguments, load validated settings, enforce safe path relationships, and invoke the pipeline. `lumen_ex.cli.main` is the executable interface. |
| Input discovery / ingestion | `lumen_ex.ingestion.StreamingIngestor` walks inputs in stable order, rejects symlinks, hashes source files in chunks, detects supported encodings, prevalidates each file, and returns per-file ingestion or quarantine outcomes. CSV/TSV rows retain raw headers/cells; JSON record arrays use escaped JSON Pointers; standalone FHIR R4 resources, complete FHIR Bundles, and text files use `doc`. Parsed records are lazy and record values are hidden from repr. |
| Shared contracts | Hold file metadata, normalized records with optional identity projections and source structure, typed processing outcomes, linkage decisions, manifest entries, and aggregate reports without depending on format-specific algorithms. |
| Normalization | Normalize representation while retaining source locators and medical values. `Normalizer.normalize` accepts a `ParsedRecord` and returns a `NormalizedRecord`; `PatientIdentityNormalizer` provides the identity-projection implementation. |
| PII detection | `lumen_ex.detection.MultilingualPIIDetector` scans structured fields, free text, keys, headers, and filenames for context-labelled identity fields and validated direct identifiers. It returns typed, offset-bearing findings with values and source keys hidden from repr. It is deterministic, offline, and does not alter records; it is not a general-purpose NER model. |
| Identity extraction and normalization | `lumen_ex.identity` projects parsed row/object values, FHIR R4 Patient resources or Bundles, and explicitly labeled English, Hindi, or Hinglish note fields into compact `PatientIdentity` values. It includes email and explicitly labeled relative names alongside identity/insurance identifiers, and patient-contact phones are distinguished from clinician context. `PatientIdentityNormalizer` attaches projections without changing parsed source fields. |
| Identity registry storage | `lumen_ex.registry.EncryptedIdentityRegistry` stores encrypted evidence in one per-run SQLite database and keyed block indexes for candidate retrieval. It is shared across batches, removes its private database directory on close, and refuses cluster merges with transitive same-hospital identifier conflicts. |
| Indic name normalization | `lumen_ex.detection.indic_names` normalizes supplied Latin/Devanagari name candidates with ITRANS transliteration, Unicode, punctuation, a small honorific vocabulary, and a limited set of common Mohammad spelling variants. It preserves initials and token order; name equality is not proof of identity. |
| Candidate matching | `lumen_ex.matching.IdentityMatcher` retrieves a bounded set of indexed candidates and applies deterministic evidence rules. A unique strong identifier, or a name plus DOB, relative name, or village and age (within one year), can support a link; names alone cannot. Ambiguous or conflicting evidence stays unresolved. `IdentityLinker` adapts results to the pipeline's LINK/DO_NOT_LINK/ABSTAIN contract. Registry cluster aliases are merged only when same-hospital identifier conflicts are absent across both transitive families. |
| Privacy transformation | `lumen_ex.privacy.PrivacyTransformer` applies keyed pseudonyms only for LINK decisions, per-patient date shifts, truthful age bands, conservative home-location generalization, finding-based identifier redaction, and cohort quasi-identifier assessment. `transform_batch` is the release-capable API; its outputs can be sanitized, suppressed, or quarantined. |
| Leak scanning / security | `lumen_ex.security.SensitiveDataLeakScanner` reuses the M04 detector for structured values, text, headers, filenames, JSON/JSONL/CSV/TSV, reports, logs supplied for scanning, and on-disk files. The starter adapter scans serialized results and record metadata in memory before writing. Scan failures fail closed. |
| Output writing | Serialize approved results while preserving the official output contract. `OutputWriter` is the format-specific boundary. |
| Reporting | Aggregate safe counts, hashes, durations, and resource metrics. `Reporter` must not include source names, record values, or exception text. |
| Validation / testing | Unit tests exercise contracts and coordination; official validators remain outside the application and are not modified. |

Pipeline stages depend on the shared contracts rather than one another's internal
implementations. The coordinator processes records in order, sends only leak-scanned
sanitized results to the writer, reports every result, and then finalizes the report.
Its stage dependencies are injected, so later milestones can add implementations
without changing the contracts or creating microservices.

The M06 transformer requires cohort context to make its minimum-group-size decision.
Its `transform` adapter processes a singleton and therefore fails closed by suppressing
it when the configured minimum cannot be met. `transform_batch` generalizes the cohort,
measures equivalence classes and rare values, suppresses permitted quasi-identifiers
cohort-wide, then suppresses any remaining record whose group is still below the
configured minimum. The existing `Pipeline.run` is still record-at-a-time, so a future
integration must provide cohort-aware orchestration before the modular transformer can
be used as a submission path.

## M06 transformation details

The privacy package shares purpose-separated HKDF key derivation with the encrypted
registry. Pseudonyms and date shifts use distinct derived keys and HMAC domain tags;
unlinked/abstained records receive no patient pseudonym and use a record-local date
shift reference instead. Dates are shifted by one deterministic offset per patient or
unresolved record, preserving time intervals. Ambiguous or malformed date-like values
fail closed rather than being silently emitted unchanged.

Age bands use the configured width below 85 and an open `85+` band above it. Residence
location follows the configured village → tehsil → district → state hierarchy, drops
street-level fields, and retains hospital/facility locations. Clinical fields (including
diagnoses, medications, doses, frequencies, vitals, labs, procedures, and stay
intervals) are checked for preservation. Rare clinical values are never individually
rewritten; if the configured risk threshold cannot be met through permitted QI
suppression, the record is explicitly suppressed.

Risk figures are cohort-specific estimates over recognized quasi-identifier fields,
not a security guarantee or a formal guarantee against linkage. The transformer does
not implement output serialization, leak scanning, cryptographic erasure, or the
submission CLI integration.

The identity helpers consume parsed values from the ingestion boundary. JSON discovery
selects root arrays and arrays under common collection keys; otherwise a JSON document
is one record. Each file is prevalidated before the lazy record stream is exposed, and
one unsupported or malformed file does not prevent outcomes for other files. The
ingestor performs basic FHIR resource and Bundle structure checks, not full profile or
terminology validation. Free-text extraction is limited to explicit field labels; it is
not a general name recognizer and does not perform record linking. The ingestion API is
available through `Pipeline.ingest_directory`; the production CLI still uses the
starter adapter to preserve its official output behavior and applies the M07 output
guard at that boundary.

The registry provides encrypted storage, exact keyed block lookup, and streaming
candidate iteration. Candidate scoring is a separate stage and does not merge patients.
Global cluster reconciliation remains a later stage. The matcher uses a bounded
candidate set; overflow is unresolved rather than truncated to a potentially misleading
best match. Its database is per-run and must be closed to remove the encrypted database
and SQLite sidecars.

## Operational constraints

The command requires `RAKSHA_SECRET`, injects it into the starter for one invocation,
and clears the module-level secret reference afterward. The scanner canary is also
cleared after processing. Logs are limited to safe error codes and aggregate starter
counts. Processing errors carry codes rather than source text. The ingestion API returns
safe per-file error codes without raw file contents; it does not write or modify input
files. These are engineering choices, not a claim that the current baseline removes PII
reliably or provides a complete security guarantee; see [security limitations](security.md).
