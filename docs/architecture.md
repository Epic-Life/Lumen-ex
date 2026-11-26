# Lumen-ex architecture (foundation through detection)

Lumen-ex is a single-process Python application. It runs locally, offline, on CPU;
there are no services, model downloads, or runtime network dependencies. The root
`main.py` is the evaluator entry point and `lumen_ex` contains the application
foundation. The official starter remains available under `starter/` and is currently
called through a narrow compatibility adapter.

## Data flow

```text
raksha.toml → main.py / CLI → validated policy
                            → starter compatibility adapter (current baseline)

Modular processing flow:
Pipeline.ingest_directory → per-file ingestion outcomes → parsed records
Parsed records → normalization → PII detection → record linkage
                → privacy transformation → leak scan → output writer → report
```

PII detection now has a deterministic implementation; record linkage and later privacy
stages remain extension points. The detection engine is not wired into the production
CLI or an output transformation in this milestone.
The current adapter preserves the shipped starter's basic behavior and its known
limitations; it is not a production-ready firewall.

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

The identity helpers consume parsed values from the ingestion boundary. JSON discovery
selects root arrays and arrays under common collection keys; otherwise a JSON document
is one record. Each file is prevalidated before the lazy record stream is exposed, and
one unsupported or malformed file does not prevent outcomes for other files. The
ingestor performs basic FHIR resource and Bundle structure checks, not full profile or
terminology validation. Free-text extraction is limited to explicit field labels; it is
not a general name recognizer and does not perform record linking. The ingestion API is
available through `Pipeline.ingest_directory`, but the production CLI still uses the
starter adapter to preserve its official output behavior.

The registry provides encrypted storage, exact keyed block lookup, and streaming
candidate iteration. Candidate scoring is a separate stage and does not merge patients.
Global cluster reconciliation remains a later stage. The matcher uses a bounded
candidate set; overflow is unresolved rather than truncated to a potentially misleading
best match. Its database is per-run and must be closed to remove the encrypted database
and SQLite sidecars.

## Operational constraints

The command requires `RAKSHA_SECRET` and does not print or persist it. Logs are limited to safe error codes and aggregate starter counts. Processing errors
carry codes rather than source text. The ingestion API returns safe per-file error codes
without raw file contents; it does not write or modify input files. These are
engineering choices, not a claim that the current baseline removes PII reliably or
provides a complete security guarantee.
