import base64
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HiddenChannelsTest(unittest.TestCase):
    def test_renames_hidden_fields_and_updates_fhir_references(self):
        patient_id = "9876543210"
        name = "Example Patient"
        attachment = base64.b64encode(
            f"Referral: {name} (TEST-091234), mobile {patient_id}, treat fever.".encode()
        ).decode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            output = root / "output"
            inputs.mkdir()
            (inputs / f"{patient_id}_results.csv").write_text(
                "time,TEST-091234 Example\n09:45,36.8\n"
            )
            (inputs / "bundle.json").write_text(json.dumps({
                "resourceType": "Bundle",
                "entry": [
                    {"fullUrl": "https://example.test/Patient/" + patient_id, "resource": {
                        "resourceType": "Patient", "id": patient_id,
                        "name": [{"text": name, "given": ["Example"], "family": "Patient"}],
                    }},
                    {"resource": {
                        "resourceType": "DocumentReference",
                        "subject": {"reference": f"Patient/{patient_id}", "display": name},
                        "content": [{"attachment": {"contentType": "text/plain", "data": attachment}}],
                    }},
                ],
                "meta": {"source_path": rf"C:\\Exports\\{name}_{patient_id}"},
            }))
            (inputs / "index.json").write_text(json.dumps({
                "by_name": {name: "TEST-091234"}, "by_uhid": {"TEST-091234": {"visits": 2}},
            }))
            subprocess.run(
                [sys.executable, "main.py", "--input", str(inputs), "--output", str(output)],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, RAKSHA_SECRET="test-secret"),
                check=True,
                capture_output=True,
            )
            sanitized = output / "sanitized"
            filenames = sorted(path.name for path in sanitized.iterdir())
            self.assertNotIn(f"{patient_id}_results.csv", filenames)
            with next(sanitized.glob("*.csv")).open(newline="") as stream:
                header, row = list(csv.reader(stream))
            self.assertEqual(header[0], "time")
            self.assertTrue(header[1].startswith("column_"))
            self.assertEqual(row, ["09:45", "36.8"])
            bundle = json.loads((sanitized / "bundle.json").read_text())
            patient, document = (entry["resource"] for entry in bundle["entry"])
            self.assertEqual(document["subject"]["reference"], f"Patient/{patient['id']}")
            self.assertNotEqual(patient["id"], patient_id)
            decoded = base64.b64decode(document["content"][0]["attachment"]["data"]).decode()
            self.assertIn("treat fever.", decoded)
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertNotIn(patient_id, path.read_text())
                    self.assertNotIn(name, path.read_text())
                    self.assertNotIn("TEST-091234", path.read_text())
            self.assertNotIn(name, decoded)
            self.assertNotIn(patient_id, decoded)
