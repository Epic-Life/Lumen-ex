import tempfile
import unittest
from pathlib import Path

from lumen_ex.contracts.identity import IdentityDecision, IdentityValue, PatientIdentity
from lumen_ex.detection.indic_names import normalize_name
from lumen_ex.matching import IdentityMatcher
from lumen_ex.registry import EncryptedIdentityRegistry


_SECRET = b"synthetic-matching-test-secret"
_SOURCE_SHA = "b" * 64


def _identity(
    locator: str,
    *,
    name: str | None = "Asha Sharma",
    dob: str | None = "1982-04-13",
    phone: str | None = "+919876543210",
    hospital_id: str | None = None,
    government_id: str | None = None,
) -> PatientIdentity:
    def values(value: str | None, key: str) -> tuple[IdentityValue, ...]:
        normalized = normalize_name(value) if value and key == "Name" else value
        return (IdentityValue(value, normalized, key),) if value else ()

    return PatientIdentity(
        source_file="synthetic.csv",
        record_locator=locator,
        name=values(name, "Name"),
        date_of_birth=values(dob, "DOB"),
        phone=values(phone, "Phone"),
        hospital_identifier=values(hospital_id, "PatientID"),
        government_identifier=values(government_id, "HealthID"),
    )


class IdentityMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.registry = EncryptedIdentityRegistry.temporary(
            _SECRET, Path(self.temporary.name)
        )
        self.matcher = IdentityMatcher(
            self.registry, hospital_namespace="synthetic-hospital"
        )

    def tearDown(self) -> None:
        self.registry.close()
        self.temporary.cleanup()

    def _store(
        self,
        locator: str,
        identity: PatientIdentity,
        namespace: str = "synthetic-hospital",
    ) -> str:
        patient_id = self.registry.create_patient(_SOURCE_SHA, locator)
        self.registry.add_record_mapping(
            _SOURCE_SHA, locator, patient_id, IdentityDecision.CREATE, 0.9
        )
        self.registry.add_identity(
            patient_id,
            _SOURCE_SHA,
            identity,
            namespace=namespace,
        )
        return patient_id

    def test_links_exact_hospital_identifier_with_independent_name_evidence(self) -> None:
        existing = _identity("row:0", hospital_id="HOSP-1")
        expected = self._store("row:0", existing)

        result = self.matcher.evaluate(
            _identity("row:1", name="Asha Sharma", hospital_id="HOSP-1")
        )

        self.assertEqual(result.decision, IdentityDecision.LINK)
        self.assertEqual(result.patient_id, expected)
        self.assertEqual(result.reason, "unique_best_candidate")
        self.assertIn("hospital_identifier_exact", result.candidates[0].evidence)

    def test_name_similarity_alone_never_links_and_sparse_records_stay_unresolved(self) -> None:
        self._store(
            "row:0",
            _identity(
                "row:0",
                name="Asha Sharma",
                dob=None,
                phone=None,
                hospital_id=None,
            ),
        )

        result = self.matcher.evaluate(
            _identity("row:1", name="Asha Sharma", dob=None, phone=None)
        )

        self.assertEqual(result.decision, IdentityDecision.UNRESOLVED)
        self.assertIsNone(result.patient_id)
        self.assertEqual(result.reason, "weak_candidate_evidence")

    def test_phone_dob_and_cross_script_name_can_together_support_link(self) -> None:
        existing = _identity(
            "row:0",
            name="Asha Sharma",
            phone="+919876543210",
            hospital_id=None,
        )
        expected = self._store("row:0", existing)

        result = self.matcher.evaluate(
            _identity(
                "row:1",
                name="आशा शर्मा",
                phone="+919876543210",
                hospital_id=None,
            )
        )

        self.assertEqual(result.decision, IdentityDecision.LINK)
        self.assertEqual(result.patient_id, expected)

    def test_same_namespace_hospital_identifier_conflict_blocks_link(self) -> None:
        self._store(
            "row:0",
            _identity("row:0", hospital_id="HOSP-1", government_id="GOV-1"),
        )

        result = self.matcher.evaluate(
            _identity(
                "row:1",
                hospital_id="HOSP-2",
                government_id="GOV-1",
            )
        )

        self.assertEqual(result.decision, IdentityDecision.UNRESOLVED)
        self.assertEqual(result.reason, "hard_identifier_conflict")
        self.assertTrue(result.candidates[0].hard_conflict)

    def test_strong_identifier_does_not_override_a_clear_name_conflict(self) -> None:
        self._store(
            "row:0",
            _identity("row:0", name="Asha Sharma", hospital_id="HOSP-1"),
        )

        result = self.matcher.evaluate(
            _identity("row:1", name="Rahul Verma", hospital_id="HOSP-1")
        )

        self.assertEqual(result.decision, IdentityDecision.UNRESOLVED)
        self.assertIn("name_conflict", result.candidates[0].evidence)

    def test_missing_stored_name_is_not_treated_as_a_name_conflict(self) -> None:
        existing = _identity("row:0", name=None, hospital_id="HOSP-1")
        expected = self._store("row:0", existing)

        result = self.matcher.evaluate(
            _identity("row:1", name="Asha Sharma", hospital_id="HOSP-1")
        )

        self.assertEqual(result.decision, IdentityDecision.LINK)
        self.assertEqual(result.patient_id, expected)

    def test_conflicting_hospital_ids_in_different_namespaces_do_not_conflict(self) -> None:
        first = self._store(
            "row:0",
            _identity("row:0", hospital_id="HOSP-1", government_id="GOV-1"),
            namespace="origin-hospital",
        )
        matcher = IdentityMatcher(self.registry, hospital_namespace="incoming-hospital")

        result = matcher.evaluate(
            _identity(
                "row:1",
                hospital_id="OTHER-HOSP-1",
                government_id="GOV-1",
            )
        )

        self.assertEqual(result.decision, IdentityDecision.LINK)
        self.assertEqual(result.patient_id, first)

    def test_ambiguous_equal_candidates_remain_unresolved(self) -> None:
        first = _identity("row:0", hospital_id="HOSP-1")
        second = _identity("row:1", hospital_id="HOSP-1")
        self._store("row:0", first)
        self._store("row:1", second)

        result = self.matcher.evaluate(
            _identity("row:2", hospital_id="HOSP-1")
        )

        self.assertEqual(result.decision, IdentityDecision.UNRESOLVED)
        self.assertEqual(result.reason, "ambiguous_candidates")

    def test_unmatched_strong_identifier_creates_but_weak_identity_does_not(self) -> None:
        strong = self.matcher.evaluate(
            _identity("row:0", hospital_id="NEW-MRN")
        )
        weak = self.matcher.evaluate(
            _identity("row:1", name="Asha Sharma", dob=None, phone=None)
        )

        self.assertEqual(strong.decision, IdentityDecision.CREATE)
        self.assertEqual(strong.reason, "no_existing_candidate")
        self.assertEqual(weak.decision, IdentityDecision.UNRESOLVED)

    def test_candidate_budget_overflow_abstains_without_materializing_more(self) -> None:
        for index in range(3):
            locator = f"row:{index}"
            self._store(
                locator,
                _identity(locator, hospital_id=None, government_id="SHARED-GOV"),
            )
        matcher = IdentityMatcher(
            self.registry,
            hospital_namespace="synthetic-hospital",
            candidate_limit=2,
        )

        result = matcher.evaluate(
            _identity("row:3", hospital_id=None, government_id="SHARED-GOV")
        )

        self.assertEqual(result.decision, IdentityDecision.UNRESOLVED)
        self.assertEqual(result.reason, "candidate_limit_exceeded")
        self.assertEqual(result.candidates, ())

    def test_invalid_thresholds_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IdentityMatcher(self.registry, link_threshold=0)


if __name__ == "__main__":
    unittest.main()
