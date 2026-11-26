"""Conservative indexed candidate scoring; this module never merges patients."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import math

from lumen_ex.contracts.identity import IdentityDecision, IdentityValue, PatientIdentity
from lumen_ex.detection.indic_names import normalize_name
from lumen_ex.registry import EncryptedIdentityRegistry


_BLOCK_FIELDS = (
    "government_identifier",
    "hospital_identifier",
    "phone",
    "date_of_birth",
    "name",
)
_STRONG_IDENTIFIERS = frozenset({"government_identifier", "hospital_identifier"})
_MIN_NAME_CORROBORATION = 0.65
_MIN_NAME_CONFLICT = 0.55
_DEFAULT_LINK_THRESHOLD = 0.85
_DEFAULT_AMBIGUITY_MARGIN = 0.05
_DEFAULT_CANDIDATE_LIMIT = 500


@dataclass(frozen=True)
class CandidateScore:
    patient_id: str = field(repr=False)
    confidence: float
    evidence: tuple[str, ...]
    hard_conflict: bool = False


@dataclass(frozen=True)
class IdentityMatchResult:
    decision: IdentityDecision
    patient_id: str | None = field(repr=False)
    confidence: float
    reason: str
    candidates: tuple[CandidateScore, ...] = ()


class IdentityMatcher:
    """Retrieve bounded candidates and decide LINK, CREATE, or UNRESOLVED.

    Confidence is a deterministic evidence score, not a calibrated probability.
    Names can corroborate independent evidence but can never link records alone.
    """

    def __init__(
        self,
        registry: EncryptedIdentityRegistry,
        hospital_namespace: str = "",
        link_threshold: float = _DEFAULT_LINK_THRESHOLD,
        ambiguity_margin: float = _DEFAULT_AMBIGUITY_MARGIN,
        candidate_limit: int = _DEFAULT_CANDIDATE_LIMIT,
    ) -> None:
        if (
            isinstance(link_threshold, bool)
            or not isinstance(link_threshold, (int, float))
            or not math.isfinite(link_threshold)
            or not 0.0 < link_threshold <= 1.0
        ):
            raise ValueError("link_threshold_invalid")
        if (
            isinstance(ambiguity_margin, bool)
            or not isinstance(ambiguity_margin, (int, float))
            or not math.isfinite(ambiguity_margin)
            or not 0.0 <= ambiguity_margin < 1.0
        ):
            raise ValueError("ambiguity_margin_invalid")
        if (
            isinstance(candidate_limit, bool)
            or not isinstance(candidate_limit, int)
            or candidate_limit < 1
        ):
            raise ValueError("candidate_limit_invalid")
        if not isinstance(hospital_namespace, str):
            raise ValueError("hospital_namespace_invalid")

        self.registry = registry
        self.hospital_namespace = hospital_namespace
        self.link_threshold = float(link_threshold)
        self.ambiguity_margin = float(ambiguity_margin)
        self.candidate_limit = candidate_limit

    def evaluate(self, identity: PatientIdentity) -> IdentityMatchResult:
        """Score only indexed candidates; do not persist a decision or merge."""
        values = _normalized_values(identity)
        candidates: dict[str, set[str]] = {}
        for field_name in _BLOCK_FIELDS:
            if field_name == "hospital_identifier" and not self.hospital_namespace:
                continue
            namespace = (
                self.hospital_namespace if field_name == "hospital_identifier" else ""
            )
            for value in values[field_name]:
                for patient_id in self.registry.iter_resolved_candidate_ids(
                    field_name, value, namespace
                ):
                    candidates.setdefault(patient_id, set()).add(field_name)
                    if len(candidates) > self.candidate_limit:
                        return IdentityMatchResult(
                            IdentityDecision.UNRESOLVED,
                            None,
                            0.0,
                            "candidate_limit_exceeded",
                        )

        if not candidates:
            if _sufficient_for_create(values, bool(self.hospital_namespace)):
                return IdentityMatchResult(
                    IdentityDecision.CREATE, None, 0.9, "no_existing_candidate"
                )
            return IdentityMatchResult(
                IdentityDecision.UNRESOLVED,
                None,
                0.0,
                "insufficient_identity_evidence",
            )

        scores = tuple(
            sorted(
                (
                    self._score_candidate(patient_id, block_matches, values)
                    for patient_id, block_matches in candidates.items()
                ),
                key=lambda candidate: (-candidate.confidence, candidate.patient_id),
            )
        )
        eligible = tuple(
            candidate
            for candidate in scores
            if not candidate.hard_conflict
            and "name_conflict" not in candidate.evidence
            and candidate.confidence >= self.link_threshold
            and _has_independent_support(candidate.evidence)
        )
        if not eligible:
            reason = (
                "hard_identifier_conflict"
                if all(candidate.hard_conflict for candidate in scores)
                else "weak_candidate_evidence"
            )
            return IdentityMatchResult(
                IdentityDecision.UNRESOLVED, None, scores[0].confidence, reason, scores
            )
        if (
            len(eligible) > 1
            and eligible[0].confidence - eligible[1].confidence
            < self.ambiguity_margin
        ):
            return IdentityMatchResult(
                IdentityDecision.UNRESOLVED,
                None,
                eligible[0].confidence,
                "ambiguous_candidates",
                scores,
            )
        best = eligible[0]
        return IdentityMatchResult(
            IdentityDecision.LINK,
            best.patient_id,
            best.confidence,
            "unique_best_candidate",
            scores,
        )

    def _score_candidate(
        self,
        patient_id: str,
        block_matches: set[str],
        values: dict[str, set[str]],
    ) -> CandidateScore:
        exact_fields = set(block_matches)
        best_name_similarity = 0.0
        has_stored_name = False
        for field_name, stored_value in self.registry.iter_identity_values(patient_id):
            normalized = stored_value.normalized
            if normalized is None:
                continue
            if field_name == "name":
                stored_name = normalize_name(normalized)
                has_stored_name = has_stored_name or bool(stored_name)
                for current_name in values["name"]:
                    best_name_similarity = max(
                        best_name_similarity,
                        _name_similarity(current_name, stored_name),
                    )
            elif normalized in values.get(field_name, set()):
                exact_fields.add(field_name)

        evidence = set(f"{field_name}_exact" for field_name in exact_fields)
        if best_name_similarity >= _MIN_NAME_CORROBORATION:
            evidence.add("name_similar")
        elif (
            values["name"]
            and has_stored_name
            and best_name_similarity < _MIN_NAME_CONFLICT
        ):
            evidence.add("name_conflict")

        hard_conflict = False
        if self.hospital_namespace:
            for identifier in values["hospital_identifier"]:
                if self.registry.has_conflicting_block(
                    patient_id,
                    "hospital_identifier",
                    identifier,
                    self.hospital_namespace,
                ):
                    hard_conflict = True
                    evidence.add("hospital_identifier_conflict")
                    break

        confidence = _evidence_confidence(exact_fields, best_name_similarity)
        if hard_conflict:
            confidence = 0.0
        return CandidateScore(
            patient_id=patient_id,
            confidence=confidence,
            evidence=tuple(sorted(evidence)),
            hard_conflict=hard_conflict,
        )


def _normalized_values(identity: PatientIdentity) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for field_name in _BLOCK_FIELDS:
        values: tuple[IdentityValue, ...] = getattr(identity, field_name)
        normalized_values = {
            value.normalized
            for value in values
            if isinstance(value.normalized, str) and value.normalized
        }
        if field_name == "name":
            normalized_values = {
                normalized
                for value in values
                if isinstance(value.normalized, str)
                and (normalized := normalize_name(value.normalized))
            }
        result[field_name] = normalized_values

    for field_name in ("gender", "age", "address", "location"):
        result[field_name] = {
            value.normalized
            for value in getattr(identity, field_name)
            if isinstance(value.normalized, str) and value.normalized
        }
    return result


def _evidence_confidence(exact_fields: set[str], name_similarity: float) -> float:
    confidence = 1.0
    weights = {
        "government_identifier": 0.92,
        "hospital_identifier": 0.92,
        "phone": 0.60,
        "date_of_birth": 0.60,
        "gender": 0.10,
        "age": 0.05,
        "address": 0.05,
        "location": 0.05,
    }
    for field_name, weight in weights.items():
        if field_name in exact_fields:
            confidence *= 1.0 - weight
    confidence *= 1.0 - (0.40 * name_similarity)
    return min(1.0, max(0.0, 1.0 - confidence))


def _name_similarity(first: str, second: str) -> float:
    if first == second:
        return 1.0
    if len(first) > 512 or len(second) > 512:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def _has_independent_support(evidence: tuple[str, ...]) -> bool:
    evidence_set = set(evidence)
    strong_identifier = bool(
        evidence_set & {f"{field}_exact" for field in _STRONG_IDENTIFIERS}
    )
    name_support = "name_similar" in evidence_set
    other_support = bool(evidence_set & {"phone_exact", "date_of_birth_exact"})
    if strong_identifier and (name_support or other_support):
        return True
    return (
        "phone_exact" in evidence_set
        and "date_of_birth_exact" in evidence_set
        and name_support
    )


def _sufficient_for_create(
    values: dict[str, set[str]], has_hospital_namespace: bool
) -> bool:
    has_identifier = bool(values["government_identifier"]) or (
        has_hospital_namespace and bool(values["hospital_identifier"])
    )
    return has_identifier or bool(
        values["phone"] and values["date_of_birth"] and values["name"]
    )


__all__ = ["CandidateScore", "IdentityMatchResult", "IdentityMatcher"]
