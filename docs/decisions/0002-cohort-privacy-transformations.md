# ADR 0002: Cohort-aware privacy transformations

## Status

Accepted for M06.

## Context

Patient pseudonyms and date shifts need a deterministic patient reference, while
minimum-group-size and rare-value decisions require a cohort. A per-record-only
interface cannot make a release decision safely. The existing official output
contract must remain unchanged, and the submission CLI must retain its starter
behavior until later integration work.

## Decision

- Implement the M06 transformations as an injectable `lumen_ex.privacy` package.
- Provide a cohort API that first applies permitted generalization, assesses
  quasi-identifier equivalence classes, suppresses rare permitted fields cohort-wide,
  and explicitly suppresses records that remain below the policy threshold.
- Use HKDF purpose-separated keys and HMAC domain separation for pseudonyms and date
  shifts; unresolved records use a record-local reference and never receive a patient
  pseudonym.
- Keep output serialization, leak scanning, and official CLI integration outside M06.

## Alternatives considered

- **Per-record k-anonymity:** rejected because it cannot know the cohort size and would
  either over-release or require silent, arbitrary defaults.
- **Change the official record/report schema:** rejected because M06's internal fields
  must not change the evaluator contract.
- **Integrate the transformation into the starter CLI now:** rejected because the
  starter has its own output contract behavior and M06 does not authorize replacing
  that path.

## Consequences

The modular privacy stage is independently testable, but `transform` on one record
fails closed when the configured group size cannot be established. A later milestone
must add cohort-aware orchestration and a leak scanner before this stage can be used as
a complete submission pipeline. Risk metrics are estimates, not security guarantees.
