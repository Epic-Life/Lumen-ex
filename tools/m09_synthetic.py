"""Run a labelled synthetic release and compare it with independent attacker facts."""

import argparse
import csv
from datetime import date
import hashlib
import io
from itertools import combinations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from tools.synthetic_attacker import evaluate_external_population


CANARY = "m09.canary.8426@example.invalid"
CLINICAL = {
    "diagnosis": "hypertension",
    "medicine": "amlodipine",
    "dose": "5 mg",
    "frequency": "once daily",
    "vitals": "120/80 mmHg",
    "lab_result": "4.25 mmol/L",
    "procedure": "ECG",
}


def run(root: Path) -> dict[str, object]:
    source = root / "input"
    output = root / "output"
    temporary = root / "tmp"
    source.mkdir()
    temporary.mkdir()
    # External evidence is generated before release. Patient names and contact
    # details deliberately do not participate in the attack.
    population = {
        "A": {"age": {"42"}, "gender": {"f"}, "village": {"small hamlet"},
              "pincode": {"248111"}, "hospital": {"clinic east", "clinic west"},
              "diagnosis": {"hypertension"}, "procedure": {"ecg"},
              "stay": {"2"}, "date": {"2025-05-10"}},
        "B": {"age": {"43"}, "gender": {"f"}, "village": {"other hamlet"},
              "pincode": {"248112"}, "hospital": {"clinic east"},
              "diagnosis": {"hypertension"}, "procedure": {"ecg"}},
        "C": {"age": {"87"}, "gender": {"m"}, "village": {"small hamlet"},
              "pincode": {"248113"}, "hospital": {"clinic west"},
              "diagnosis": {"porphyria"}, "procedure": {"ecg"}},
    }
    for index in range(12):
        population[f"decoy-{index}"] = {
            "age": {str(40 + index % 5)},
            "gender": {"f" if index % 2 else "m"},
            "village": {"other hamlet"},
            "pincode": {"248112"},
            "hospital": {"clinic east"} if index < 6 else {"clinic west"},
            "diagnosis": {"hypertension"},
            "procedure": {"ecg"},
            "stay": {"1"},
        }
    rows = []
    labels = []
    for index in range(12):
        label = "A" if index < 5 else "B" if index < 10 else "C" if index == 10 else "U"
        labels.append(label)
        facts = population.get(label, {})
        linked = label != "U"
        rows.append({
            **CLINICAL,
            "name": ("मीना राव" if index % 2 else "Mina Rav") if label == "A" else
                    "Dipa Sen" if label == "B" else "Mina Rav" if linked else "",
            "aadhaar": {"A": "234567891215", "B": "234567891223", "C": "234567891231"}.get(label, ""),
            "phone": {"A": "9876542810", "B": "9876542811", "C": "9876542812"}.get(label, ""),
            "email": CANARY if label == "A" else "",
            "age": next(iter(facts.get("age", {""}))),
            "gender": next(iter(facts.get("gender", {""}))),
            "village": next(iter(facts.get("village", {""}))),
            "pincode": next(iter(facts.get("pincode", {""}))),
            "hospital": "Clinic West" if label == "C" or (label == "A" and index % 2) else "Clinic East",
            "diagnosis": "porphyria" if label == "C" else CLINICAL["diagnosis"],
            "admission_date": "2025-05-10" if label == "A" else "",
            "discharge_date": "2025-05-12" if label == "A" else "",
            "length_of_stay": "2" if label == "A" else "",
        })
    inputs = io.StringIO(newline="")
    writer = csv.DictWriter(inputs, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    data = inputs.getvalue().encode()
    (source / "visits.csv").write_bytes(data)
    attachment = source / f"{CANARY}.pdf"
    attachment.write_bytes(b"%PDF-1.4\n" + CANARY.encode())
    command = [
        sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "main.py"),
        "--input", str(source), "--output", str(output),
    ]
    child = subprocess.run(command, capture_output=True, check=False, timeout=900, env={
        **os.environ, "RAKSHA_SECRET": "m09-synthetic-release", "TMPDIR": str(temporary),
        "TEMP": str(temporary), "TMP": str(temporary),
    })
    if child.returncode:
        raise RuntimeError(f"synthetic pipeline exit {child.returncode}")
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    visits = next(item for item in report["files"] if item["input_sha256"] == hashlib.sha256(data).hexdigest())
    mappings = [json.loads(line) for line in (output / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    answer = {(visits["output"], f"row:{index}"): label for index, label in enumerate(labels)}
    observed = []
    values_kept = values_exported = stays_kept = original_dates_changed = 0
    if visits["output"]:
        with (output / visits["output"]).open(encoding="utf-8", newline="") as stream:
            exported = list(csv.DictReader(stream))
        for mapping in mappings:
            original_index = int(mapping["loc"][4:])
            exported_index = int(mapping.get("out", mapping["loc"])[4:])
            actual = exported[exported_index]
            original = rows[original_index]
            observed.append((labels[original_index], mapping["patient"]))
            for field in CLINICAL:
                values_exported += 1
                values_kept += actual.get(field) == original[field]
            if labels[original_index] == "A":
                admission = date.fromisoformat(actual["admission_date"])
                discharge = date.fromisoformat(actual["discharge_date"])
                stays_kept += (discharge - admission).days == 2
                original_dates_changed += str(admission) != original["admission_date"]
    tp = fp = 0
    for (first, first_id), (second, second_id) in combinations(observed, 2):
        if first_id is not None and first_id == second_id:
            tp += first == second
            fp += first != second
    true_pairs = sum(first == second for first, second in combinations(labels, 2))
    marker = CANARY.encode()
    leaked = marker in child.stdout + child.stderr or any(
        marker in str(path.relative_to(directory)).encode() or
        (path.is_file() and marker in path.read_bytes())
        for directory in (output, temporary) for path in directory.rglob("*")
    )
    return {
        "input_records": len(rows), "exported_records": len(observed), "canary_leaked": leaked,
        "release_counts": report["counts"], "visits_status": visits["status"],
        "visits_reason": visits.get("reason"),
        "record_attack": evaluate_external_population(output, population, answer, aggregate=False, ignore_dates=True),
        "profile_attack": evaluate_external_population(output, population, answer, aggregate=True, ignore_dates=True),
        "exact_date_attack": evaluate_external_population(output, population, answer, aggregate=True),
        "clinical_values_kept": values_kept, "clinical_values_exported": values_exported,
        "clinical_values_input": len(rows) * len(CLINICAL), "stays_kept": stays_kept,
        "admission_dates_changed": original_dates_changed,
        "linkage": {"true_positive_pairs": tp, "false_positive_pairs": fp,
                    "false_negative_pairs": true_pairs - tp,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / true_pairs if true_pairs else None,
                    "null_mappings": sum(patient is None for _, patient in observed)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lumen-m09-") as directory:
        result = run(Path(directory))
    print(json.dumps(result, indent=2))
    return int(result["canary_leaked"])


if __name__ == "__main__":
    raise SystemExit(main())
