import csv
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class GeneralizationTest(unittest.TestCase):
    def test_cli_generalizes_demographics_and_preserves_stay_length(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            inputs.mkdir()
            (inputs / "patients.csv").write_text(
                "UHID,Age,Village/Mohalla,District,PIN,Admission Date,Discharge Date,Dose\n"
                "HOSP-124578,48,Sample Village,Sample District,229073,2025-02-01,2025-02-10,650 mg\n",
                encoding="utf-8",
            )
            (inputs / "note.txt").write_text(
                "UHID: HOSP-124578\nDate of Admission: 2025-02-01\n",
                encoding="utf-8",
            )

            def run(dirname, secret):
                output = root / dirname
                subprocess.run(
                    [sys.executable, "main.py", "--input", str(inputs), "--output", str(output)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=dict(os.environ, RAKSHA_SECRET=secret),
                    check=True,
                    capture_output=True,
                )
                with (output / "sanitized/patients.csv").open(newline="") as stream:
                    row = next(csv.DictReader(stream))
                return output, row

            output, row = run("one", "first-secret")
            self.assertEqual(row["Age"], "45-49")
            self.assertEqual(row["Village/Mohalla"], "Sample District")
            self.assertEqual(row["PIN"], "229XXX")
            self.assertEqual(row["Dose"], "650 mg")
            start = datetime.fromisoformat(row["Admission Date"])
            end = datetime.fromisoformat(row["Discharge Date"])
            self.assertEqual((end - start).days, 9)
            self.assertNotEqual(row["Admission Date"], "2025-02-01")
            note = (output / "sanitized/note.txt").read_text()
            self.assertIn(row["Admission Date"], note)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["counts"]["records_generalized"], 2)
            _, changed = run("two", "second-secret")
            self.assertNotEqual(changed["Admission Date"], row["Admission Date"])
