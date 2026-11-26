import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliLinkageTest(unittest.TestCase):
    def test_links_across_formats_and_abstains_on_a_name_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            inputs.mkdir()
            (inputs / "patients.csv").write_text(
                "Patient Name,Phone,Aadhaar No,Dose\n"
                "Mohammad Shahid,+91 98765 43210,741258963215,650 mg\n"
                "Mohammad Shahid,+91 98765 43210,152639487511,500 mg\n",
                encoding="utf-8",
            )
            (inputs / "note.txt").write_text(
                "नाम: मोहम्मद शाहिद\nफोन: +91 98765 43210\nआधार: 741258963215\n",
                encoding="utf-8",
            )
            (inputs / "ambiguous.txt").write_text("Patient: Mohammad Shahid\n", encoding="utf-8")

            def run(output, secret):
                subprocess.run(
                    [sys.executable, "main.py", "--input", str(inputs), "--output", str(output)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=dict(os.environ, RAKSHA_SECRET=secret),
                    check=True,
                    capture_output=True,
                )
                return {
                    (entry["file"], entry["loc"]): entry["patient"]
                    for line in (output / "records.jsonl").read_text().splitlines()
                    for entry in [json.loads(line)]
                }

            output = root / "first"
            linked = run(output, "secret-a")
            pid = linked["sanitized/note.txt", "doc"]
            self.assertRegex(pid, r"^PSN-[0-9A-Z]+$")
            self.assertEqual(linked["sanitized/patients.csv", "row:0"], pid)
            self.assertIsNone(linked["sanitized/patients.csv", "row:1"])
            self.assertIsNone(linked["sanitized/ambiguous.txt", "doc"])
            with (output / "sanitized/patients.csv").open(newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["Patient Name"], pid)
            self.assertEqual(row["Dose"], "650 mg")
            self.assertIn(pid, (output / "sanitized/note.txt").read_text())
            self.assertEqual(run(root / "repeat", "secret-a"), linked)
            self.assertNotEqual(run(root / "different", "secret-b")["sanitized/note.txt", "doc"], pid)
