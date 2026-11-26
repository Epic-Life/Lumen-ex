"""Measure release coverage and linkage against the Team Kit practice labels."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath

from check_dev import norm, read_text, record_for


def measure(output: Path, truth_path: Path) -> dict[str, object]:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    manifest = defaultdict(list)
    for entry in report["files"]:
        manifest[entry["input_sha256"]].append(entry["output"])
    mappings = defaultdict(dict)
    for line in (output / "records.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        mappings[item["file"]][item["loc"]] = item["patient"]

    kinds = defaultdict(Counter)
    actual_leaks = Counter()
    clusters = defaultdict(set)
    required = 0
    exported_required = 0
    null_required = 0
    pair_cells = Counter()
    label_counts = Counter()
    pseudonym_counts = Counter()
    all_units = defaultdict(set)
    for relative, source in truth["files"].items():
        candidates = manifest[source["sha256"]]
        same_path = f"sanitized/{relative}"
        # A digest is ambiguous for byte-identical renamed files. Preserve
        # same-path matches first and report this ambiguity separately.
        released = same_path if same_path in candidates else candidates[0] if len(candidates) == 1 else None
        text = norm(read_text(output / released)) if released else ""
        name = norm(str(PurePosixPath(released).relative_to("sanitized"))) if released else ""
        for locator, unit in source["units"].items():
            for entity in unit["entities"]:
                surface = norm(entity["surface"])
                searchable = name if entity["channel"] == "filename" else text
                if surface and surface in searchable:
                    actual_leaks[entity["channel"]] += 1
            for value in unit.get("preserve", []):
                count = kinds[value["kind"]]
                count["input"] += 1
                if released:
                    count["in_released_file"] += 1
                    count["found"] += bool(norm(value["text"]) in text)
            patient = unit.get("patient")
            if unit.get("link") != "required" or not patient:
                continue
            required += 1
            label_counts[patient] += 1
            key = (relative, locator)
            all_units[patient].add(key)
            if not released or not any(
                loc == "doc" or loc == locator or locator.startswith(loc + "/")
                for loc in mappings[released]
            ):
                continue
            exported_required += 1
            pseudo = record_for(locator, mappings[released])
            if not pseudo:
                null_required += 1
                continue
            clusters[pseudo].add(key)
            pseudonym_counts[pseudo] += 1
            pair_cells[patient, pseudo] += 1

    def pairs(counts):
        return sum(n * (n - 1) // 2 for n in counts.values())

    tp = pairs(pair_cells)
    predicted = pairs(pseudonym_counts)
    expected = pairs(label_counts)
    truth_clusters = {frozenset(members) for members in all_units.values()}
    output_clusters = {frozenset(members) for members in clusters.values()}
    return {
        "required_link_units": required,
        "exported_required_units": exported_required,
        "exported_required_null": null_required,
        "pairwise": {"tp": tp, "fp": predicted - tp, "fn": expected - tp,
                     "precision": tp / predicted if predicted else None,
                     "recall": tp / expected if expected else None},
        "exact_clusters": len(truth_clusters & output_clusters),
        "true_clusters": len(truth_clusters),
        "duplicate_digest_groups": sum(len(entries) > 1 for entries in manifest.values()),
        "actual_export_matches": dict(actual_leaks),
        "clinical": {kind: dict(counts) for kind, counts in sorted(kinds.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--truth", type=Path, default=Path("dev/truth/truth.json"))
    args = parser.parse_args()
    print(json.dumps(measure(args.output, args.truth), indent=2))


if __name__ == "__main__":
    main()
