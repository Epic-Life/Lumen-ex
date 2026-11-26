# M07 security controls and limitations

## Secret handling

The root CLI requires `RAKSHA_SECRET` from the process environment. It passes the
secret to the starter only for the current invocation, clears the starter's module
reference in a `finally` block, and clears the leak scanner's canary reference after
processing. Secrets are not written to reports, logs, or temporary files. Python string
and byte objects cannot be reliably zeroized; clearing references is not proof of
physical memory erasure.

The encrypted identity registry derives separate keys for database encryption and
blind-index lookup using HKDF-SHA-256. It encrypts persisted identity evidence with
AES-GCM, fresh 12-byte nonces, and associated data. A per-run temporary SQLite
directory is removed on close; SQLite temporary storage is configured for memory and
`secure_delete` is enabled. `secure_delete` and filesystem deletion do not guarantee
physical erasure on SSDs, snapshots, journals, backups, or every operating system.
Temporary directory permissions use the Python runtime defaults; Windows ACLs are not
customized by this project.

## Leak scanning

`SensitiveDataLeakScanner` reuses the existing offline M04 detector. It scans
structured values and keys, CSV/TSV headers and values, JSON/JSONL, free text,
filenames, reports, and caller-supplied log text. The starter adapter checks each
transformed artifact and its record metadata in memory before writing it. It then
checks the complete `records.jsonl`, report, and count-only stdout summary before
release. Detected or unscannable individual artifacts are quarantined with safe reason
codes; metadata scan failures stop report generation with a nonzero CLI result.

The scanner is bounded to 64 MiB per artifact by default; JSON artifacts over 4 MiB or
structured streams with more than 100,000 items fail closed to limit parser memory and
work. It rejects symlinks and unreadable files. SQLite artifacts are opened read-only;
schema and text cells are scanned, and opaque binary cells are checked for configured
canaries; valid UTF-8/UTF-16 text inside binary cells is scanned as well. Encrypted
registry values are not decrypted by the scanner; encryption and
at-rest tests independently verify those values are not stored as plaintext.
Synthetic test canaries can be supplied in memory and are hidden from the scanner's
representation.

## Limitations

- The M04 detector is deterministic and rule-based, not a complete PII recognizer. An
  unrecognized identifier can pass the scanner.
- Generic opaque binary formats are rejected rather than decoded. Arbitrary BLOB
  content in SQLite is not semantically inspected; the scanner checks canary bytes.
- The production CLI still uses the starter's basic transformation rather than the
  cohort-aware M06 transformer. The scanner can quarantine recognized leaks but does
  not make the starter a complete privacy firewall.
- Per-file artifacts are written only after their content and record metadata pass.
  If a later report-level check fails, previously written artifacts remain in the
  output directory; they have each passed the per-file scan, but the run returns an
  error and may not satisfy the output validator.
- Secret references are cleared, not guaranteed erased from process memory. Temporary
  file deletion and SQLite `secure_delete` are best effort, not cryptographic erasure.
- No custom Windows ACL, memory locking, core-dump control, or filesystem-level secure
  deletion is implemented.

These controls reduce accidental disclosure and fail closed at the implemented
boundaries. They are not a formal security proof or a guarantee of zero re-identification
risk.
