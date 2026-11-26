"""Temporal value helpers for privacy transformations and interval checks."""

from datetime import datetime

from lumen_ex.value_normalization import normalize_date


def normalize_temporal_date(value: str) -> str | None:
    if "T" in value or "t" in value or (" " in value and ":" in value):
        try:
            normalized = value.replace("Z", "+00:00").replace("z", "+00:00")
            return datetime.fromisoformat(normalized).date().isoformat()
        except ValueError:
            return None
    return normalize_date(value)
