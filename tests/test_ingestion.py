import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lumen_ex.errors import ProcessingError
from lumen_ex.ingestion import StreamingIngestor


class StreamingIngestorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "input"
        self.root.mkdir()
        self.ingestor = StreamingIngestor()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_discovery_is_sorted_and_hashes_files_incrementally(self) -> None:
        second = self.root / "z.json"
        first = self.root / "a.txt"
        second.write_text("{}", encoding="utf-8")
        first.write_text("note", encoding="utf-8")

        sources = list(self.ingestor.discover(self.root))

        self.assertEqual(
            [source.relative_path for source in sources],
            ["a.txt", "z.json"],
        )
        self.assertEqual(
            sources[0].sha256,
            hashlib.sha256(b"note").hexdigest(),
        )
        self.assertEqual(sources[0].media_type, "text/plain")

    def test_csv_tsv_and_batches_preserve_zero_based_row_locators(self) -> None:
        (self.root / "a.csv").write_text(
            "Patient Name,Age\nAsha,30\nBina,31\n", encoding="utf-8"
        )
        (self.root / "b.tsv").write_text(
            "Patient Name\tAge\nChandra\t32\n", encoding="utf-8"
        )

        batches = list(self.ingestor.iter_batches(self.root, batch_size=2))

        self.assertEqual([len(batch) for batch in batches], [2, 1])
        self.assertEqual(
            [(record.source.relative_path, record.locator) for batch in batches for record in batch],
            [("a.csv", "row:0"), ("a.csv", "row:1"), ("b.tsv", "row:0")],
        )
        self.assertEqual(batches[0][0].values["Patient Name"], "Asha")

    def test_decodes_utf8_bom_utf16_and_cp1252(self) -> None:
        examples = (
            ("utf8.csv", "Name\nAsha\n".encode("utf-8-sig"), "Asha"),
            ("utf16.csv", "Name\nBina\n".encode("utf-16"), "Bina"),
            ("legacy.csv", "Name\nJosé\n".encode("cp1252"), "José"),
        )
        for filename, contents, expected in examples:
            with self.subTest(filename=filename):
                path = self.root / filename
                path.write_bytes(contents)
                source = next(
                    item
                    for item in self.ingestor.discover(self.root)
                    if item.relative_path == filename
                )
                record = next(self.ingestor.read(source))
                self.assertEqual(record.values["Name"], expected)

    def test_streams_nested_json_arrays_and_escapes_pointer_segments(self) -> None:
        path = self.root / "nested.json"
        path.write_text(
            json.dumps(
                {
                    "metadata": {"source": "synthetic"},
                    "records": [
                        {"patient": {"name": "Asha"}},
                        {"patient": {"name": "Bina"}},
                    ],
                }
            ),
            encoding="utf-8",
        )
        source = next(self.ingestor.discover(self.root))

        records = list(self.ingestor.read(source))

        self.assertEqual([record.locator for record in records], ["/records/0", "/records/1"])
        self.assertEqual(records[0].values["patient"]["name"], "Asha")

    def test_streams_root_arrays_and_single_json_documents(self) -> None:
        array_path = self.root / "array.json"
        array_path.write_text('[{"name":"Asha"},{"name":"Bina"}]', encoding="utf-8")
        array_source = next(self.ingestor.discover(self.root))
        array_records = list(self.ingestor.read(array_source))
        self.assertEqual([record.locator for record in array_records], ["/0", "/1"])

        document_path = self.root / "patient.json"
        document_path.write_text('{"resourceType":"Patient","name":[{"text":"Asha"}]}', encoding="utf-8")
        document_source = next(
            item
            for item in self.ingestor.discover(self.root)
            if item.relative_path == "patient.json"
        )
        document = next(self.ingestor.read(document_source))
        self.assertEqual(document.locator, "doc")
        self.assertEqual(document.values["resourceType"], "Patient")

    def test_bundle_emits_patient_resources_with_bundle_pointers(self) -> None:
        path = self.root / "bundle.json"
        path.write_text(
            json.dumps(
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {"resource": {"resourceType": "Observation", "id": "obs"}},
                        {"resource": {"resourceType": "Patient", "id": "patient"}},
                    ],
                }
            ),
            encoding="utf-8",
        )
        source = next(self.ingestor.discover(self.root))

        records = list(self.ingestor.read(source))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].locator, "/entry/1/resource")
        self.assertEqual(records[0].values["id"], "patient")

    def test_text_note_is_one_document_record(self) -> None:
        path = self.root / "note.txt"
        path.write_text("Name: Asha\nAge: 30", encoding="utf-8")
        source = next(self.ingestor.discover(self.root))

        record = next(self.ingestor.read(source))

        self.assertEqual(record.locator, "doc")
        self.assertEqual(record.values["text"], "Name: Asha\r\nAge: 30")

    def test_empty_unsupported_and_malformed_inputs_fail_with_safe_codes(self) -> None:
        (self.root / "empty.csv").write_bytes(b"")
        (self.root / "unsupported.xml").write_text("<root/>", encoding="utf-8")
        (self.root / "broken.json").write_text('{"records":[', encoding="utf-8")
        sources = {item.relative_path: item for item in self.ingestor.discover(self.root)}

        with self.assertRaises(ProcessingError) as empty:
            next(self.ingestor.read(sources["empty.csv"]))
        self.assertEqual(empty.exception.code, "empty_file")

        with self.assertRaises(ProcessingError) as unsupported:
            next(self.ingestor.read(sources["unsupported.xml"]))
        self.assertEqual(unsupported.exception.code, "unsupported_format")

        with self.assertRaises(ProcessingError) as malformed:
            list(self.ingestor.read(sources["broken.json"]))
        self.assertEqual(malformed.exception.code, "JSONDecodeError")

    def test_read_requires_a_discovered_input_root_and_batch_size_is_positive(self) -> None:
        (self.root / "example.txt").write_text("note", encoding="utf-8")
        with self.assertRaises(ProcessingError) as not_discovered:
            self.ingestor.read(
                # The record is irrelevant; source resolution must fail safely first.
                next(StreamingIngestor().discover(self.root))
            )
        self.assertEqual(not_discovered.exception.code, "invalid_record")
        with self.assertRaises(ValueError):
            list(self.ingestor.iter_batches(self.root, batch_size=0))


if __name__ == "__main__":
    unittest.main()
