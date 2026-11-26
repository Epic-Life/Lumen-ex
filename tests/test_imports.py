import subprocess
import sys
from pathlib import Path
import unittest


class ImportOrderTests(unittest.TestCase):
    def test_identity_import_does_not_depend_on_detection_import_order(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import lumen_ex.identity; "
                    "from lumen_ex.detection import MultilingualPIIDetector; "
                    "assert MultilingualPIIDetector"
                ),
            ],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
