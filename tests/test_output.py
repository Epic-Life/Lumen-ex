import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lumen_ex.config import PipelineConfig
from lumen_ex.contracts.models import (
    InputFileMetadata,
    InputFormat,
    ParsedRecord,
    ProcessingStatus,
    SanitizationResult,
)
from lumen_ex.output import (
    OutputContractValidator,
    ReleaseRecord,
    SanitizedOutputWriter,
)
from lumen_ex.release import ReleasePipeline
from lumen_ex.security import SensitiveDataLeakScanner
from lumen_ex.identity import PatientIdentityNormalizer


class SanitizedOutputWriterTests(unittest.TestCase):
    def test_csv_keeps_header_and_places_pseudonym_in_existing_identifier_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            output_root = root / "output"
            input_root.mkdir()
            (output_root / "sanitized").mkdir(parents=True)
            raw = b"name,phone,diagnosis\nMina Rao,9876543210,hypertension\n"
            (input_root / "records.csv").write_bytes(raw)
            source = _metadata("records.csv", raw)
            parsed = ParsedRecord(
                source,
                "row:0",
                {
                    "name": "Mina Rao",
                    "phone": "9876543210",
                    "diagnosis": "hypertension",
                },
                {
                    "headers": ("name", "phone", "diagnosis"),
                    "cells": ("Mina Rao", "9876543210", "hypertension"),
                },
            )
            normalized = PatientIdentityNormalizer().normalize(parsed)
            result = SanitizationResult(
                ProcessingStatus.SANITIZED,
                "row:0",
                {
                    "name": "[REDACTED]",
                    "phone": "[REDACTED]",
                    "diagnosis": "hypertension",
                },
                output_structure={"headers": ["name", "phone", "diagnosis"]},
                output_relative_path="sanitized/records.csv",
                patient_pseudonym="PSN-0123456789ABCDEF",
            )
            writer = SanitizedOutputWriter(
                input_root, output_root, SensitiveDataLeakScanner()
            )

            written = writer.write_file(
                source, [ReleaseRecord(parsed, normalized, result)]
            )

            self.assertEqual(
                written.manifest.status,
                ProcessingStatus.SANITIZED,
                written.manifest.reason,
            )
            self.assertEqual(written.records[0]["patient"], "PSN-0123456789ABCDEF")
            with (output_root / "sanitized" / "records.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], ["name", "phone", "diagnosis"])
            self.assertEqual(rows[1][1], "PSN-0123456789ABCDEF")
            self.assertEqual(rows[1][2], "hypertension")

    def test_json_suppression_remaps_output_locator_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            output_root = root / "output"
            input_root.mkdir()
            (output_root / "sanitized").mkdir(parents=True)
            raw = b'{"visits":[{"diagnosis":"discarded"},{"diagnosis":"stable"}]}'
            (input_root / "visits.json").write_bytes(raw)
            source = _metadata("visits.json", raw)
            first = ParsedRecord(
                source, "/visits/0", {"diagnosis": "discarded"}
            )
            second = ParsedRecord(
                source, "/visits/1", {"diagnosis": "stable"}
            )
            first_normalized = PatientIdentityNormalizer().normalize(first)
            second_normalized = PatientIdentityNormalizer().normalize(second)
            suppressed = SanitizationResult(
                ProcessingStatus.SUPPRESSED, "/visits/0", suppressed=True
            )
            sanitized = SanitizationResult(
                ProcessingStatus.SANITIZED,
                "/visits/1",
                {"diagnosis": "stable"},
                output_relative_path="sanitized/visits.json",
                generalized=True,
            )
            writer = SanitizedOutputWriter(
                input_root, output_root, SensitiveDataLeakScanner()
            )

            written = writer.write_file(
                source,
                [
                    ReleaseRecord(first, first_normalized, suppressed),
                    ReleaseRecord(second, second_normalized, sanitized),
                ],
            )

            self.assertEqual(written.manifest.status, ProcessingStatus.SANITIZED)
            self.assertEqual(written.records[0]["loc"], "/visits/1")
            self.assertEqual(written.records[0]["out"], "/visits/0")
            self.assertEqual(
                json.loads((output_root / "sanitized" / "visits.json").read_text())[
                    "visits"
                ],
                [{"diagnosis": "stable"}],
            )
            self.assertEqual((input_root / "visits.json").read_bytes(), raw)

    def test_fhir_patient_pseudonym_preserves_bundle_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            output_root = root / "output"
            input_root.mkdir()
            (output_root / "sanitized").mkdir(parents=True)
            document = {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [
                    {
                        "fullUrl": "urn:uuid:p1",
                        "resource": {
                            "resourceType": "Patient",
                            "id": "p1",
                            "identifier": [
                                {"system": "urn:mrn", "value": "[REDACTED]"}
                            ],
                        },
                    },
                    {
                        "fullUrl": "urn:uuid:o1",
                        "resource": {
                            "resourceType": "Observation",
                            "id": "o1",
                            "subject": {"reference": "urn:uuid:p1"},
                        },
                    },
                ],
            }
            raw = (json.dumps(document) + "\n").encode("utf-8")
            (input_root / "bundle.json").write_bytes(raw)
            source = InputFileMetadata(
                "bundle.json",
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                detected_format=InputFormat.FHIR,
                encoding="utf-8",
            )
            parsed = ParsedRecord(source, "doc", document)
            normalized = PatientIdentityNormalizer().normalize(parsed)
            result = SanitizationResult(
                ProcessingStatus.SANITIZED,
                "doc",
                document,
                output_relative_path="sanitized/bundle.json",
                patient_pseudonym="PSN-0123456789ABCDEF",
            )
            writer = SanitizedOutputWriter(
                input_root, output_root, SensitiveDataLeakScanner()
            )

            written = writer.write_file(
                source, [ReleaseRecord(parsed, normalized, result)]
            )

            self.assertEqual(
                written.manifest.status,
                ProcessingStatus.SANITIZED,
                written.manifest.reason,
            )
            self.assertEqual(written.records[0]["patient"], "PSN-0123456789ABCDEF")
            output_document = json.loads(
                (output_root / "sanitized" / "bundle.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                output_document["entry"][0]["resource"]["identifier"][0]["value"],
                "PSN-0123456789ABCDEF",
            )
            self.assertEqual(
                output_document["entry"][1]["resource"]["subject"]["reference"],
                "urn:uuid:p1",
            )

    def test_quarantines_duplicate_csv_headers_without_losing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            output_root = root / "output"
            input_root.mkdir()
            (output_root / "sanitized").mkdir(parents=True)
            raw = b"name,name\nMina Rao,second value\n"
            (input_root / "duplicate.csv").write_bytes(raw)
            source = _metadata("duplicate.csv", raw)
            parsed = ParsedRecord(
                source,
                "row:0",
                {"name": "second value"},
                {
                    "headers": ("name", "name"),
                    "cells": ("Mina Rao", "second value"),
                },
            )
            normalized = PatientIdentityNormalizer().normalize(parsed)
            result = SanitizationResult(
                ProcessingStatus.SANITIZED,
                "row:0",
                {"name": "[REDACTED]"},
                output_structure={"headers": ["name", "name"]},
                output_relative_path="sanitized/duplicate.csv",
            )

            written = SanitizedOutputWriter(
                input_root, output_root, SensitiveDataLeakScanner()
            ).write_file(source, [ReleaseRecord(parsed, normalized, result)])

            self.assertEqual(written.manifest.status, ProcessingStatus.QUARANTINED)
            self.assertEqual(written.manifest.reason, "format_mismatch")
            self.assertFalse((output_root / "sanitized" / "duplicate.csv").exists())

    def test_release_pipeline_writes_scanned_cohort_and_valid_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            output_root = root / "output"
            input_root.mkdir()
            rows = ["name,phone,age,diagnosis"]
            for index in range(5):
                rows.append(
                    f"Patient {index},987654321{index},40,hypertension"
                )
            (input_root / "cohort.csv").write_text(
                "\n".join(rows) + "\n", encoding="utf-8"
            )
            scanner = SensitiveDataLeakScanner(canaries=("test-secret",))

            report = ReleasePipeline(
                input_root,
                output_root,
                PipelineConfig(),
                "test-secret",
                scanner,
            ).run()

            report_data = json.loads(
                (output_root / "report.json").read_text(encoding="utf-8")
            )
            record_lines = (
                output_root / "records.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(report.records_processed, 5)
            self.assertEqual(report_data["counts"]["records_processed"], 5)
            self.assertEqual(len(record_lines), 5)
            self.assertTrue(scanner.scan_tree(output_root / "sanitized"))
            records = [json.loads(line) for line in record_lines]
            self.assertTrue(all(record["patient"] for record in records))
            self.assertTrue(all(record["file"] == "sanitized/cohort.csv" for record in records))
            OutputContractValidator().validate(
                output_root, report.files, records, report_data
            )
            second_output = root / "output-repeat"
            ReleasePipeline(
                input_root,
                second_output,
                PipelineConfig(),
                "test-secret",
                SensitiveDataLeakScanner(canaries=("test-secret",)),
            ).run()
            self.assertEqual(
                (output_root / "sanitized" / "cohort.csv").read_bytes(),
                (second_output / "sanitized" / "cohort.csv").read_bytes(),
            )
            self.assertEqual(
                (output_root / "records.jsonl").read_bytes(),
                (second_output / "records.jsonl").read_bytes(),
            )


def _metadata(relative_path: str, raw: bytes) -> InputFileMetadata:
    return InputFileMetadata(
        relative_path=relative_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
