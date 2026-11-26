"""Devanagari-aware normalization for comparing known personal-name candidates.

This module does not discover names in arbitrary text. Callers must supply candidate
names from a trusted source and apply their own contextual rules before redaction.
"""

import re
import unicodedata

from indic_transliteration import sanscript


_DEVANAGARI = re.compile(r"[\u0900-\u097f]")
_DEVANAGARI_RUN = re.compile(r"([\u0900-\u097f]+)")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_FIRST_NAME_VARIANTS = {
    "mohammad": "mohammad",
    "mohammed": "mohammad",
    "muhammad": "mohammad",
    "mohd": "mohammad",
    "md": "mohammad",
}


def contains_devanagari(text: str) -> bool:
    """Return whether text contains at least one Devanagari code point."""
    return _DEVANAGARI.search(text) is not None


def transliterate_to_latin(text: str) -> str:
    """Transliterate Devanagari runs to ITRANS, preserving other text."""
    text = unicodedata.normalize("NFKC", text)
    return _DEVANAGARI_RUN.sub(
        lambda match: sanscript.transliterate(
            match.group(), sanscript.DEVANAGARI, sanscript.ITRANS
        ),
        text,
    )


def normalize_name(name: str) -> str:
    """Return a conservative comparison key while preserving name order and initials."""
    tokens: list[str] = []
    normalized = unicodedata.normalize("NFKC", name)
    for segment in _DEVANAGARI_RUN.split(normalized):
        if not segment:
            continue
        is_devanagari = contains_devanagari(segment)
        converted = (
            sanscript.transliterate(
                segment, sanscript.DEVANAGARI, sanscript.ITRANS
            )
            if is_devanagari
            else _strip_combining_marks(segment)
        )
        for token in _WORD.findall(converted):
            folded = _fold_token(token, remove_inherent_a=is_devanagari)
            if folded:
                tokens.append(folded)

    while tokens and tokens[0] in _PREFIX_HONORIFICS:
        tokens.pop(0)
    if tokens:
        tokens[0] = _FIRST_NAME_VARIANTS.get(tokens[0], tokens[0])
    if len(tokens) > 1 and tokens[-1] in _SUFFIX_HONORIFICS:
        tokens.pop()
    return " ".join(tokens)


def names_match(first: str, second: str) -> bool:
    """Compare complete names after script and Unicode normalization."""
    first_key = normalize_name(first)
    return bool(first_key) and first_key == normalize_name(second)


def _fold_token(token: str, *, remove_inherent_a: bool) -> str:
    # ITRANS uses uppercase vowels for long sounds. A final lowercase "a" in a
    # Devanagari transliteration is the Hindi schwa often omitted in Latin spelling.
    if remove_inherent_a and token.endswith("a"):
        token = token[:-1]
    folded = token.casefold().replace("ś", "sh").replace("ṣ", "sh")
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _strip_combining_marks(text: str) -> str:
    text = (
        text.replace("Ś", "sh")
        .replace("ś", "sh")
        .replace("Ṣ", "sh")
        .replace("ṣ", "sh")
        .replace("S\u030c", "sh")
        .replace("s\u030c", "sh")
        .replace("S\u0323", "sh")
        .replace("s\u0323", "sh")
    )
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )


_PREFIX_HONORIFICS = frozenset(
    {"mr", "mrs", "ms", "sri", "shri", "smt", "shrimati"}
)
_SUFFIX_HONORIFICS = frozenset(
    {"ji", "sahab", "sahib", "didi", "behenji"}
)