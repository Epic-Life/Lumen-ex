import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from lumen_ex.key_derivation import derive_key
from lumen_ex.security import SensitiveDataLeakScanner


_CANARY = "SYNTHETIC-CANARY-ALPHA-8427"


class SensitiveDataLeakScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = SensitiveDataLeakScanner(canaries=(_CANARY,))

    def tearDown(self) -> None:
        self.scanner.clear_canaries()

    def test_key_derivation_is_deterministic_and_purpose_separated(self) -> None:
        encryption = derive_key("synthetic-secret", "encryption")
        self.assertEqual(encryption, derive_key("synthetic-secret", "encryption"))
        self.assertNotEqual(
            encryption, derive_key("synthetic-secret", "blind-index")
        )
        self.assertNotEqual(
            encryption, derive_key("different-secret", "encryption")
        )

    def test_scans_text_values_metadata_and_filenames_for_canaries(self) -> None:
        self.assertFalse(self.scanner.scan_text(f"log marker {_CANARY}"))
        self.assertFalse(self.scanner.scan_value({"metadata": {"value": _CANARY}}))
        self.assertFalse(
            self.scanner.scan_artifact_text("{}", filename=f"{_CANARY}.json")
        )
        self.assertTrue(self.scanner.scan_value({"metadata": "safe"}))
        self.assertNotIn(_CANARY, repr(self.scanner))

    def test_scans_release_artifacts_against_file_scoped_sensitive_values(self) -> None:
        sensitive_value = "SYNTHETIC-PATIENT-IDENTIFIER-71"
        self.assertFalse(
            self.scanner.scan_artifact_text(
                json.dumps({"note": f"Reference {sensitive_value}"}),
                filename="records.json",
                sensitive_values=(sensitive_value,),
            )
        )
        self.assertTrue(
            self.scanner.scan_artifact_text(
                json.dumps({"note": "Clinical text without identifiers"}),
                filename="records.json",
                sensitive_values=(sensitive_value,),
            )
        )

    def test_scans_structured_json_keys_csv_headers_and_values(self) -> None:
        self.assertFalse(
            self.scanner.scan_artifact_text(
                '{"9876543210": "not a patient field"}',
                filename="records.json",
            )
        )
        self.assertFalse(
            self.scanner.scan_artifact_text(
                "patient_name,medication\nAsha Sharma,Paracetamol 500 mg\n",
                filename="records.csv",
            )
        )
        self.assertFalse(
            self.scanner.scan_artifact_text(
                '{"contact": {"phone": "9876543210"}}',
                filename="resource.json",
            )
        )

    def test_preserves_clinical_content_without_identifier_findings(self) -> None:
        content = (
            '{"diagnosis": "Parkinson disease", '
            '"medication": "Paracetamol 500 mg", "dose": "1-0-1"}'
        )
        self.assertTrue(
            self.scanner.scan_artifact_text(content, filename="clinical.json")
        )

    def test_treats_official_sha256_fields_as_hashes_not_raw_identifiers(self) -> None:
        digest = "9876543210" + "a" * 54
        report = {"files": [{"input_sha256": digest, "status": "sanitized"}]}
        serialized = json.dumps(report)
        self.assertTrue(
            self.scanner.scan_artifact_text(serialized, filename="report.json")
        )
        self.assertTrue(self.scanner.scan_value(report))

    def test_accepts_release_placeholders_and_aggregated_entity_counts(self) -> None:
        report = {
            "counts": {"records_processed": 5},
            "entities": {"AGE": 5, "PATIENT_NAME": 5, "PHONE": 5},
        }
        self.assertTrue(
            self.scanner.scan_artifact_text(
                json.dumps(report), filename="report.json"
            )
        )
        self.assertTrue(
            self.scanner.scan_artifact_text(
                "name,phone\n[REDACTED],PSN-0123456789ABCDEF\n",
                filename="sanitized/records.csv",
            )
        )
        self.assertFalse(
            self.scanner.scan_text(f"Name: [REDACTED] {_CANARY}")
        )

    def test_scans_files_and_fails_closed_for_unsupported_or_symlinked_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe_file = root / "safe.txt"
            safe_file.write_text("Clinical note without identifiers.", encoding="utf-8")
            self.assertTrue(self.scanner.scan_path(safe_file))

            unsafe_file = root / "9876543210.txt"
            unsafe_file.write_text("No identifier in content.", encoding="utf-8")
            self.assertFalse(self.scanner.scan_path(unsafe_file))

            invalid_file = root / "invalid.txt"
            invalid_file.write_bytes(b"\xff")
            self.assertFalse(self.scanner.scan_path(invalid_file))

            link = root / "linked.txt"
            try:
                link.symlink_to(safe_file)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not available")
            self.assertFalse(self.scanner.scan_path(link))

    def test_scans_sqlite_schema_and_text_cells_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "registry.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE evidence (value TEXT)")
                connection.execute("INSERT INTO evidence VALUES (?)", ("safe",))
                connection.commit()
            finally:
                connection.close()
            self.assertTrue(self.scanner.scan_path(database))

            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "INSERT INTO evidence VALUES (?)", ("9876543210",)
                )
                connection.execute(
                    "INSERT INTO evidence VALUES (?)",
                    (sqlite3.Binary("9876543210".encode("utf-16-le")),),
                )
                connection.commit()
            finally:
                connection.close()
            self.assertFalse(self.scanner.scan_path(database))

    def test_scan_tree_rejects_sensitive_names_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "output"
            root.mkdir()
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            self.assertTrue(self.scanner.scan_tree(root))

            (root / _CANARY).mkdir()
            self.assertFalse(self.scanner.scan_tree(root))


if __name__ == "__main__":
    unittest.main()
