"""Generalize explicit demographics and shift patient event dates."""

from datetime import datetime, timedelta
import re


_AGES = {"age", "agesex", "patientage", "umar", "उम्र", "आयु"}
_PINS = {"pin", "pincode", "postalcode", "zip", "zipcode"}
_VILLAGES = {"village", "villagemohalla", "grammohalla", "gaon"}
_DATES = {
    "date", "visit", "visitdate", "admissiondate", "admittedon", "dateofadmission",
    "dischargedate", "dischargedon", "dateofdischarge", "collected", "collectedon",
    "reported", "reportedon", "registeredon", "registrationdate", "regdate",
    "registeredmmddyyyy", "dob", "dobmmddyyyy", "dateofbirth", "birthdate",
    "janmtithi", "effectivedatetime", "issued",
    "तारीख", "भर्तीकीतारीख", "छुट्टीकीतारीख", "जन्मतिथि",
}
_AGE = re.compile(r"^(?P<space>\s*)(?P<age>\d{1,3})(?=\s*(?:$|/|years?\b|yrs?\b|y\b|वर्ष))", re.I)
_DATE = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}")
_TEXT_FIELDS = re.compile(
    r"(?im)(?P<label>^[ \t]*|[|;][ \t]*)"
    r"(?P<field>age(?:[ /]sex)?|umar|उम्र|आयु|date(?: of (?:admission|discharge|birth))?|"
    r"admission date|discharge date|visit(?: date)?|dob|तारीख|भर्ती की तारीख|"
    r"छुट्टी की तारीख|जन्म तिथि|pin(?: code)?)[ \t]*[:：][ \t]*"
    r"(?P<value>[^\r\n;|]+)"
)
_AGE_IN_NOTE = re.compile(r"(?<![\w/-])(?P<age>\d{1,3})(?=\s*[Yy]\s*/\s*[MFmf]\b)")


def field_name(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum() or "\u0900" <= char <= "\u097f")


def _shift(match: re.Match, offset: int, month_first: bool) -> str:
    raw = match[0]
    separator = next(char for char in raw if char in "-/.")
    first, second, _ = raw.split(separator)
    if len(first) == 4:
        layout = "%Y", "%m", "%d"
    elif month_first or (int(first) <= 12 < int(second)):
        layout = "%m", "%d", "%Y"
    else:
        layout = "%d", "%m", "%Y"
    pattern = separator.join(layout)
    try:
        shifted = datetime.strptime(raw, pattern).date() + timedelta(days=offset)
    except (OverflowError, ValueError):
        return raw
    return shifted.strftime(pattern)


class Generalizer:
    def __init__(self, band, offset):
        self._band = band
        self._offset = offset

    def field(self, label, value, key, *, district=None, period=False):
        """Return a transformed scalar, retaining its storage type."""
        normalized = field_name(label)
        if not isinstance(value, str):
            return value
        if normalized in _AGES:
            return _AGE.sub(self._band_match, value, count=1)
        if normalized in _PINS and re.fullmatch(r"[1-9]\d{5}", value.strip()):
            return value.strip()[:3] + "XXX"
        if normalized in _VILLAGES and district and value.strip():
            return district
        if normalized in _DATES or (period and normalized in {"start", "end"}):
            return _DATE.sub(
                lambda match: _shift(match, self._offset(key), "mmddyyyy" in normalized),
                value,
                count=1,
            )
        return value

    def _band_match(self, match):
        age = int(match["age"])
        return match["space"] + self._band(age) if age <= 125 else match[0]

    def text(self, text, key):
        def transform(match):
            value = self.field(match["field"], match["value"], key)
            return match[0][:match.start("value") - match.start()] + value

        text = _TEXT_FIELDS.sub(transform, text)
        return _AGE_IN_NOTE.sub(
            lambda match: self._band(int(match["age"])) if int(match["age"]) <= 125 else match[0],
            text,
        )

    def mapping(self, record, key):
        """Apply only recognized field rules, including FHIR Period dates."""
        changed = False

        def walk(node, period=False):
            nonlocal changed
            if isinstance(node, list):
                for item in node:
                    walk(item, period)
            elif isinstance(node, dict):
                district = next(
                    (value for label, value in node.items()
                     if field_name(label) in {"district", "jila"} and isinstance(value, str) and value.strip()),
                    None,
                )
                for label, value in node.items():
                    if isinstance(value, (list, dict)):
                        walk(value, field_name(label).endswith("period"))
                    else:
                        replacement = self.field(label, value, key, district=district, period=period)
                        if replacement != value:
                            changed = True
                            node[label] = replacement

        walk(record)
        return changed
