import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliRedactionTest(unittest.TestCase):
    def test_redacts_names_and_quarantines_invalid_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, outputs = root / "input", root / "output"
            inputs.mkdir()
            (inputs / "note.txt").write_text(
                "Patient: Asha Verma | C/O fever | Dolo 650 BD x 5 days\n"
                "नाम: आशा वर्मा\n",
                encoding="utf-8",
            )
            (inputs / "bad.csv").write_text('name,dose\n"unclosed', encoding="utf-8")
            (inputs / "valid.csv").write_text(
                "Patient Name,Relative Name,Dose\nAsha Verma,Kiran Verma,650 mg\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "main.py", "--input", str(inputs), "--output", str(outputs)],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, RAKSHA_SECRET="test-secret"),
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                (outputs / "sanitized/note.txt").read_text(encoding="utf-8"),
                "Patient: [NAME] | C/O fever | Dolo 650 BD x 5 days\nनाम: [NAME]\n",
            )
            self.assertEqual(
                (outputs / "sanitized/valid.csv").read_text(encoding="utf-8"),
                "Patient Name,Relative Name,Dose\n[REDACTED],[REDACTED],650 mg\n",
            )
            report = json.loads((outputs / "report.json").read_text())
            self.assertEqual(report["counts"]["files_quarantined"], 1)
            self.assertFalse((outputs / "sanitized/bad.csv").exists())
