"""Language-aware detection helpers."""

from lumen_ex.detection.indic_names import (
    contains_devanagari,
    names_match,
    normalize_name,
    transliterate_to_latin,
)

__all__ = [
    "contains_devanagari",
    "names_match",
    "normalize_name",
    "transliterate_to_latin",
]