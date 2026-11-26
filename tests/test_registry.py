from pathlib import Path
import tempfile
import unittest

from lumen_ex.contracts.identity import (
    IdentityDecision,
    IdentityValue,
    PatientIdentity,
)
from lumen_ex.errors import ProcessingError
from lumen_ex.registry import EncryptedIdentityRegistry


_SECRET = b"synthetic-registry-test-secret"
_SOURCE_SHA = "a" * 64


def _identity(locator: str = "row:0") -> PatientIdentity:
    return PatientIdentity(
        source_file="registration/synthetic.csv",
        record_locator=locator,
        name=(IdentityValue("Sensitive Sample Name", "sensitive sample name", "Name"),),
        phone=(IdentityValue("+91 98765 43210", "+919876543210", "Phone"),),
        hospital_identifier=(
            IdentityValue("HOSP-TEST-9", "HOSPTEST9", "PatientID"),
        ),
    )


def _create_hospital_record(
    registry: EncryptedIdentityRegistry,
    locator: str,
    hospital_id: str | None,
    namespace: str,
) -> str:
    patient_id = registry.create_patient(_SOURCE_SHA, locator)
    registry.add_record_mapping(
        _SOURCE_SHA,
        locator,
        patient_id,
        IdentityDecision.CREATE,
        1.0,
    )
    identity = PatientIdentity(
        source_file="registration/synthetic.csv",
        record_locator=locator,
        hospital_identifier=(
            (IdentityValue(hospital_id, hospital_id, "PatientID"),)
            if hospital_id
            else ()
        ),
    )
    registry.add_identity(patient_id, _SOURCE_SHA, identity, namespace=namespace)
    return patient_id


class EncryptedIdentityRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parent = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.parent.cleanup()

    def _create_record(self, registry: EncryptedIdentityRegistry, locator: str = "row:0") -> str:
        patient_id = registry.create_patient(_SOURCE_SHA, locator)
        registry.add_record_mapping(
            _SOURCE_SHA,
            locator,
            patient_id,
            IdentityDecision.CREATE,
            1.0,
            {"reason": "first-seen"},
        )
        registry.add_identity(
            patient_id,
            _SOURCE_SHA,
            _identity(locator),
            namespace="synthetic-hospital",
        )
        return patient_id

    def test_encrypts_values_indexes_them_and_removes_database_on_close(self) -> None:
        with EncryptedIdentityRegistry.temporary(
            _SECRET, Path(self.parent.name)
        ) as registry:
            directory = registry.database_path.parent
            database_path = registry.database_path
            patient_id = self._create_record(registry)

            self.assertTrue(database_path.is_file())
            self.assertEqual(registry.patient_count, 1)
            self.assertEqual(registry.record_count, 1)
            self.assertEqual(
                registry.find_candidates("name", "sensitive sample name"),
                (patient_id,),
            )
            self.assertEqual(
                registry.find_candidates(
                    "hospital_identifier", "HOSPTEST9", "synthetic-hospital"
                ),
                (patient_id,),
            )
            self.assertEqual(
                registry.find_candidates(
                    "hospital_identifier", "HOSPTEST9", "different-hospital"
                ),
                (),
            )
            mapping = registry.get_record_mapping(_SOURCE_SHA, "row:0")
            self.assertEqual(mapping.patient_id, patient_id)
            self.assertEqual(mapping.decision, IdentityDecision.CREATE)
            recovered = registry.get_identity_values(patient_id)
            self.assertEqual(
                recovered["name"][0].original,
                "Sensitive Sample Name",
            )

            stored_bytes = database_path.read_bytes()
            for value in (
                b"Sensitive Sample Name",
                b"sensitive sample name",
                b"+919876543210",
                b"HOSPTEST9",
                b"registration/synthetic.csv",
                _SECRET,
            ):
                self.assertNotIn(value, stored_bytes)

        self.assertFalse(directory.exists())

    def test_internal_patient_ids_are_repeatable_for_the_same_secret_and_record(self) -> None:
        first = EncryptedIdentityRegistry.temporary(_SECRET, Path(self.parent.name))
        second = EncryptedIdentityRegistry.temporary(_SECRET, Path(self.parent.name))
        try:
            first_id = first.create_patient(_SOURCE_SHA, "row:12")
            second_id = second.create_patient(_SOURCE_SHA, "row:12")
            self.assertEqual(first_id, second_id)
            self.assertNotIn("Sensitive", repr(first))
        finally:
            first.close()
            second.close()

    def test_record_decision_constraints_and_unresolved_round_trip(self) -> None:
        with EncryptedIdentityRegistry.temporary(_SECRET, Path(self.parent.name)) as registry:
            provisional = registry.create_patient(_SOURCE_SHA, "doc")
            registry.add_record_mapping(
                _SOURCE_SHA,
                "doc",
                None,
                IdentityDecision.UNRESOLVED,
                0.2,
            )
            registry.add_identity(
                provisional,
                _SOURCE_SHA,
                _identity("doc"),
                namespace="synthetic-hospital",
            )
            mapping = registry.get_record_mapping(_SOURCE_SHA, "doc")
            self.assertEqual(mapping.patient_id, None)
            self.assertEqual(mapping.decision, IdentityDecision.UNRESOLVED)
            self.assertEqual(
                registry.find_candidates("name", "sensitive sample name"),
                (provisional,),
            )

            with self.assertRaises(ProcessingError) as missing_patient:
                registry.add_record_mapping(
                    _SOURCE_SHA, "row:1", None, IdentityDecision.CREATE, 0.8
                )
            self.assertEqual(missing_patient.exception.code, "registry_decision_invalid")

            with self.assertRaises(ProcessingError) as invalid_confidence:
                registry.add_record_mapping(
                    _SOURCE_SHA, "row:2", None, IdentityDecision.UNRESOLVED, float("nan")
                )
            self.assertEqual(
                invalid_confidence.exception.code,
                "registry_confidence_invalid",
            )

    def test_batch_transaction_rolls_back_as_one_unit(self) -> None:
        with EncryptedIdentityRegistry.temporary(_SECRET, Path(self.parent.name)) as registry:
            with self.assertRaises(RuntimeError):
                with registry.transaction():
                    self._create_record(registry)
                    raise RuntimeError("abort synthetic transaction")
            self.assertEqual(registry.patient_count, 0)
            self.assertEqual(registry.record_count, 0)

    def test_canonical_aliases_resolve_candidates_without_transitive_guessing(self) -> None:
        with EncryptedIdentityRegistry.temporary(_SECRET, Path(self.parent.name)) as registry:
            first = self._create_record(registry, "row:0")
            second = self._create_record(registry, "row:1")
            registry.set_canonical_patient(second, first)

            self.assertEqual(
                registry.find_candidates("name", "sensitive sample name"),
                (first,),
            )
            self.assertEqual(
                registry.get_record_mapping(_SOURCE_SHA, "row:1").patient_id,
                first,
            )
            with self.assertRaises(ProcessingError):
                registry.set_canonical_patient(first, second)

    def test_cluster_merge_rejects_transitive_hospital_identifier_conflicts(self) -> None:
        with EncryptedIdentityRegistry.temporary(
            _SECRET, Path(self.parent.name)
        ) as registry:
            first = _create_hospital_record(
                registry, "row:0", "HOSPITAL-A-1", "hospital-a"
            )
            bridge = _create_hospital_record(
                registry, "row:1", None, "hospital-a"
            )
            third = _create_hospital_record(
                registry, "row:2", "HOSPITAL-A-2", "hospital-a"
            )
            registry.set_canonical_patient(bridge, first)

            with self.assertRaises(ProcessingError) as conflict:
                registry.set_canonical_patient(third, bridge)

            self.assertEqual(conflict.exception.code, "registry_cluster_conflict")
            self.assertEqual(
                registry.find_candidates(
                    "hospital_identifier", "HOSPITAL-A-1", "hospital-a"
                ),
                (first,),
            )
            self.assertEqual(
                registry.find_candidates(
                    "hospital_identifier", "HOSPITAL-A-2", "hospital-a"
                ),
                (third,),
            )

    def test_cluster_merge_allows_different_hospital_namespaces(self) -> None:
        with EncryptedIdentityRegistry.temporary(
            _SECRET, Path(self.parent.name)
        ) as registry:
            first = _create_hospital_record(
                registry, "row:0", "HOSPITAL-A-1", "hospital-a"
            )
            second = _create_hospital_record(
                registry, "row:1", "HOSPITAL-B-1", "hospital-b"
            )

            registry.set_canonical_patient(second, first)

            self.assertEqual(
                registry.find_candidates(
                    "hospital_identifier", "HOSPITAL-B-1", "hospital-b"
                ),
                (first,),
            )

    def test_empty_secret_is_rejected_and_closed_registry_cannot_be_reused(self) -> None:
        with self.assertRaises(ProcessingError) as missing_secret:
            EncryptedIdentityRegistry.temporary("")
        self.assertEqual(missing_secret.exception.code, "registry_secret_missing")

        registry = EncryptedIdentityRegistry.temporary(_SECRET, Path(self.parent.name))
        registry.close()
        with self.assertRaises(ProcessingError) as closed:
            registry.find_candidates("name", "anything")
        self.assertEqual(closed.exception.code, "registry_storage_error")


if __name__ == "__main__":
    unittest.main()
