import json
from pathlib import Path
import tempfile
import unittest

from tools.synthetic_attacker import analyze_output
from tools.m09_synthetic import run


class SyntheticAttackerTests(unittest.TestCase):
    def test_pipeline_preserves_stays_and_exposes_the_expected_residual_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run(Path(temporary))

        self.assertFalse(result["canary_leaked"])
        self.assertEqual(result["exported_records"], 10)
        self.assertEqual(result["clinical_values_kept"], result["clinical_values_exported"])
        self.assertEqual(result["stays_kept"], 5)
        self.assertEqual(result["admission_dates_changed"], 5)
        self.assertEqual(result["linkage"]["false_positive_pairs"], 0)
        self.assertEqual(result["profile_attack"]["correct_records"], 5)
        self.assertEqual(result["exact_date_attack"]["correct_records"], 0)

    def test_is_deterministic_and_reads_only_exported_record_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sanitized = root / "sanitized"
            sanitized.mkdir()
            (sanitized / "records.csv").write_text(
                "age,gender,district,diagnosis\n"
                "50-54,F,East,hypertension\n"
                "60-64,M,West,diabetes\n",
                encoding="utf-8",
            )
            (root / "records.jsonl").write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {
                            "file": "sanitized/records.csv",
                            "loc": "row:0",
                            "patient": "PSN-AAA",
                        },
                        {
                            "file": "sanitized/records.csv",
                            "loc": "row:1",
                            "patient": "PSN-BBB",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            first = analyze_output(root, seed=7, population_size=20)
            second = analyze_output(root, seed=7, population_size=20)

            self.assertEqual(first, second)
            self.assertEqual(first.profiles, 2)
            self.assertEqual(first.profiles_with_evidence, 2)
            self.assertEqual(first.population, 20)
            self.assertGreaterEqual(first.mean_candidates, 1)
            self.assertLessEqual(first.unique_share, 1)

    def test_rejects_records_paths_outside_sanitized_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sanitized").mkdir()
            (root / "records.jsonl").write_text(
                '{"file":"../outside.csv","loc":"row:0","patient":null}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "records_path_invalid"):
                analyze_output(root)


if __name__ == "__main__":
    unittest.main()
