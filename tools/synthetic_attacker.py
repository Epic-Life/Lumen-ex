"""Estimate quasi-identifier uniqueness using only a sanitized release."""

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import random
import re
from typing import Mapping


_FEATURE_ALIASES = {
    "age": {"age", "patientage", "ageband", "agegroup", "umar", "उम्र", "आयु"},
    "gender": {"gender", "sex", "patientgender", "लिंग"},
    "district": {"district", "patientdistrict", "जिला"},
    "tehsil": {"tehsil", "patienttehsil"},
    "village": {"village", "patientvillage", "gaon", "गाँव"},
    "state": {"state", "patientstate", "राज्य"},
    "pincode": {"pin", "pincode", "postalcode", "zipcode"},
    "diagnosis": {"diagnosis", "condition"},
    "procedure": {"procedure"},
    "hospital": {"hospital", "hospitalname", "facility"},
    "date": {"date", "admissiondate", "visitdate", "encounterdate"},
    "stay": {"lengthofstay", "staydays", "los"},
}
_FEATURE_KEYS = {
    alias: feature
    for feature, aliases in _FEATURE_ALIASES.items()
    for alias in aliases
}
_TEXT_FIELD = re.compile(r"^\s*([^:]{1,80})\s*:\s*(.*?)\s*$")


@dataclass(frozen=True)
class SyntheticAttackReport:
    profiles: int
    population: int
    profiles_with_evidence: int
    unique_profiles: int
    mean_candidates: float
    seed: int

    @property
    def unique_share(self) -> float:
        if not self.profiles_with_evidence:
            return 0.0
        return self.unique_profiles / self.profiles_with_evidence


def analyze_output(
    output_root: Path,
    *,
    seed: int = 1701,
    population_size: int | None = None,
) -> SyntheticAttackReport:
    """Run a seeded synthetic population attack without reading input or truth data."""
    profiles = _read_profiles(output_root)
    merged = [_merge_features([fields for _, fields in group]) for group in profiles]
    evidence_profiles = [profile for profile in merged if profile]
    target_count = len(merged)
    requested_population = population_size if population_size is not None else max(target_count * 5, 1)
    if requested_population < target_count or requested_population < 1:
        raise ValueError("population_size_invalid")

    rng = random.Random(seed)
    domains: dict[str, set[str]] = {}
    for profile in evidence_profiles:
        for key, value in profile.items():
            domains.setdefault(key, set()).add(value)
    choices = {key: sorted(domain) for key, domain in domains.items()}
    decoys = [
        {key: rng.choice(domain) for key, domain in choices.items()}
        for _ in range(requested_population - target_count)
    ]
    population = merged + decoys
    candidate_counts = [
        sum(_matches(query, candidate) for candidate in population)
        for query in merged
    ]
    return SyntheticAttackReport(
        profiles=target_count,
        population=len(population),
        profiles_with_evidence=len(evidence_profiles),
        unique_profiles=sum(count == 1 for count in candidate_counts),
        mean_candidates=round(sum(candidate_counts) / target_count, 2) if target_count else 0.0,
        seed=seed,
    )


def _read_profiles(output_root: Path) -> list[list[tuple[tuple[str, str], dict[str, set[str]]]]]:
    root = output_root.resolve(strict=True)
    sanitized_root = root / "sanitized"
    if not sanitized_root.is_dir() or sanitized_root.is_symlink():
        raise ValueError("sanitized_output_missing")

    profiles = {}
    records_path = root / "records.jsonl"
    try:
        lines = records_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("records_jsonl_unreadable") from None

    for record_index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            raise ValueError("records_jsonl_invalid") from None
        if not isinstance(record, Mapping):
            raise ValueError("records_jsonl_invalid")
        relative = record.get("file")
        locator = record.get("out", record.get("loc"))
        if not isinstance(relative, str) or not isinstance(locator, str):
            raise ValueError("records_jsonl_invalid")
        artifact = _sanitized_artifact(sanitized_root, relative)
        features = _record_features(artifact, locator)
        pseudonym = record.get("patient")
        group = (
            f"patient:{pseudonym}"
            if isinstance(pseudonym, str) and pseudonym
            else f"record:{record_index}"
        )
        source_locator = record.get("loc")
        if not isinstance(source_locator, str):
            raise ValueError("records_jsonl_invalid")
        profiles.setdefault(group, []).append(((relative, source_locator), features))
    return list(profiles.values())


def evaluate_external_population(
    output_root: Path,
    population: Mapping[str, Mapping[str, set[str]]],
    answer_key: Mapping[tuple[str, str], str],
    *,
    aggregate: bool,
    ignore_dates: bool = False,
) -> dict[str, object]:
    """Match release features against independent synthetic evidence and score claims."""
    groups = _read_profiles(output_root)
    if not aggregate:
        groups = [[record] for group in groups for record in group]
    counts: Counter[int] = Counter()
    correct = false = labelled = unique_records = 0
    release_classes: Counter[tuple] = Counter()
    for group in groups:
        query: dict[str, set[str]] = {}
        for _, fields in group:
            for field, values in fields.items():
                if ignore_dates and field == "date":
                    continue
                query.setdefault(field, set()).update(values)
        release_classes[tuple((key, tuple(sorted(values))) for key, values in sorted(query.items()))] += 1
        candidates = [
            identity for identity, facts in population.items()
            if query and all(
                any(_external_match(field, observed, known) for known in facts.get(field, ()))
                for field, values in query.items()
                for observed in values
            )
        ]
        counts[len(candidates)] += 1
        labels = [answer_key[key] for key, _ in group if key in answer_key]
        labelled += len(labels)
        if len(candidates) == 1:
            unique_records += len(group)
            correct += labels.count(candidates[0])
            false += len(labels) - labels.count(candidates[0])
    return {
        "groups": len(groups),
        "labelled_records": labelled,
        "correct_records": correct,
        "false_records": false,
        "reidentification_rate": correct / labelled if labelled else None,
        "records_with_one_candidate": unique_records,
        "candidate_size_distribution": dict(sorted(counts.items())),
        "release_class_size_distribution": dict(sorted(Counter(release_classes.values()).items())),
    }


def _external_match(field: str, observed: str, known: str) -> bool:
    if field == "age" and known.isdecimal():
        band = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", observed)
        if band:
            low, high = map(int, band.groups())
            return low <= int(known) <= high
        if observed.endswith("+") and observed[:-1].isdecimal():
            return int(known) >= int(observed[:-1])
    if field == "pincode" and observed.endswith("***"):
        return known.startswith(observed[:-3])
    return observed == known


def _sanitized_artifact(sanitized_root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or path.parts[0] != "sanitized"
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("records_path_invalid")
    candidate = sanitized_root.joinpath(*path.parts[1:])
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(sanitized_root.resolve(strict=True))
    except (OSError, ValueError):
        raise ValueError("records_path_invalid") from None
    if not resolved.is_file():
        raise ValueError("records_path_invalid")
    return resolved


def _record_features(artifact: Path, locator: str) -> dict[str, set[str]]:
    suffix = artifact.suffix.casefold()
    try:
        if suffix in {".csv", ".tsv"}:
            with artifact.open("r", encoding="utf-8", newline="") as stream:
                rows = csv.reader(
                    stream, delimiter="\t" if suffix == ".tsv" else ","
                )
                headers = next(rows, [])
                if not locator.startswith("row:"):
                    raise ValueError("record_locator_invalid")
                row_index = int(locator[4:])
                values = next(
                    (row for index, row in enumerate(rows) if index == row_index),
                    None,
                )
                if values is None:
                    raise ValueError("record_locator_invalid")
            return _map_fields(dict(zip(headers, values)))
        if suffix == ".json":
            document = json.loads(artifact.read_text(encoding="utf-8"))
            return _map_fields(_resolve_json_pointer(document, locator))
        if suffix == ".txt":
            text = artifact.read_text(encoding="utf-8")
            fields = {}
            for line in text.splitlines():
                match = _TEXT_FIELD.match(line)
                if match:
                    fields[match.group(1)] = match.group(2)
            return _map_fields(fields)
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError, TypeError):
        raise ValueError("sanitized_artifact_invalid") from None
    return {}


def _resolve_json_pointer(document: object, locator: str) -> object:
    if locator == "doc":
        return document
    if not locator.startswith("/"):
        raise ValueError("record_locator_invalid")
    value = document
    for token in locator[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(key)]
        elif isinstance(value, Mapping):
            value = value[key]
        else:
            raise ValueError("record_locator_invalid")
    return value


def _map_fields(value: object) -> dict[str, set[str]]:
    features: dict[str, set[str]] = {}
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, child in item.items():
                if isinstance(key, str):
                    feature = _FEATURE_KEYS.get(
                        "".join(character for character in key.casefold() if character.isalnum())
                    )
                    normalized = _normalize_value(child)
                    if feature is not None and normalized is not None:
                        features.setdefault(feature, set()).add(normalized)
                if isinstance(child, (Mapping, list)):
                    stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return features


def _normalize_value(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    normalized = " ".join(str(value).casefold().split())
    if not normalized or normalized in {"[redacted]", "[location]", "unknown", "null"}:
        return None
    return normalized


def _merge_features(records: list[dict[str, set[str]]]) -> dict[str, str]:
    values: dict[str, set[str]] = {}
    for record in records:
        for key, candidates in record.items():
            values.setdefault(key, set()).update(candidates)
    return {
        key: next(iter(candidates))
        for key, candidates in values.items()
        if len(candidates) == 1
    }


def _matches(query: dict[str, str], candidate: dict[str, str]) -> bool:
    return all(candidate.get(key) == value for key, value in query.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--population-size", type=int)
    args = parser.parse_args()
    report = analyze_output(
        args.output,
        seed=args.seed,
        population_size=args.population_size,
    )
    print(
        "Synthetic attacker: "
        f"profiles={report.profiles} population={report.population} "
        f"with_evidence={report.profiles_with_evidence} "
        f"unique={report.unique_profiles} "
        f"unique_share={report.unique_share:.3f} "
        f"mean_candidates={report.mean_candidates:.2f} seed={report.seed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
