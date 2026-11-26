"""Language-aware detection helpers."""

from typing import TYPE_CHECKING

from lumen_ex.detection.indic_names import (
    contains_devanagari,
    names_match,
    normalize_name,
    transliterate_to_latin,
)

if TYPE_CHECKING:
    from lumen_ex.detection.pii import MultilingualPIIDetector


def __getattr__(name: str) -> object:
    if name == "MultilingualPIIDetector":
        from lumen_ex.detection.pii import MultilingualPIIDetector

        return MultilingualPIIDetector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MultilingualPIIDetector",
    "contains_devanagari",
    "names_match",
    "normalize_name",
    "transliterate_to_latin",
]