"""Identity attribute extraction from already-parsed records and labeled notes.

This module deliberately does not read files, infer unlabeled free-text names, or
decide whether two identity projections belong to the same person.
"""

from collections.abc import Mapping
from datetime import date, datetime
import re
import unicodedata
from typing import TypeAlias

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

from lumen_ex.contracts.identity import IdentityValue, PatientIdentity
from lumen_ex.detection.indic_names import normalize_name, transliterate_to_latin


_FIELD_NAMES = (
    "name",
    "date_of_birth",
    "age",
    "gender",
    "phone",
    "address",
    "location",
    "hospital_identifier",
    "government_identifier",
)
_MAX_DEPTH = 24
_MISSING_WORDS = {"", "n/a", "na", "nil", "none", "null", "unknown", "-", "--"}
_WORD_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)
_AGE = re.compile(
    r"^\s*(\d{1,3})(?:\s*(?:years?|yrs?|y|वर्ष))?"
    r"(?:\s*/\s*[mfx])?\s*$",
    re.IGNORECASE,
)
_PHONE_PUNCTUATION = frozenset(" +-()./")
_ID_PUNCTUATION = re.compile(r"[\s./-]+")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%m/%d/%Y",
    "%m-%d-%Y",
)
_ALIASES: dict[str, frozenset[str]] = {
    "name": frozenset(
        {
            "name",
            "fullname",
            "patientname",
            "patientfullname",
            "personname",
            "naam",
            "रोगीकानाम",
            "मरीजकानाम",
        }
    ),
    "date_of_birth": frozenset(
        {"dob", "dateofbirth", "birthdate", "patientdob", "जन्मतिथि"}
    ),
    "age": frozenset({"age", "patientage", "उम्र", "आयु"}),
    "gender": frozenset({"gender", "sex", "patientgender", "लिंग"}),
    "phone": frozenset(
        {
            "phone",
            "phonenumber",
            "mobile",
            "mobilenumber",
            "contactnumber",
            "telephone",
            "telephonenumber",
            "फोन",
            "मोबाइल",
        }
    ),
    "address": frozenset({"address", "patientaddress", "पता"}),
    "location": frozenset(
        {
            "city",
            "village",
            "tehsil",
            "district",
            "state",
            "location",
            "postalcode",
            "zipcode",
            "pin",
            "pincode",
            "गाँव",
            "जिला",
        }
    ),
    "hospital_identifier": frozenset(
        {
            "patientid",
            "patientidentifier",
            "hospitalid",
            "hospitalpatientid",
            "uhid",
            "mrn",
            "medicalrecordnumber",
            "registrationnumber",
            "रोगीआईडी",
        }
    ),
    "government_identifier": frozenset(
        {
            "aadhaar",
            "aadhar",
            "aadhaarno",
            "aadharno",
            "aadhaarnumber",
            "aadharnumber",
            "abha",
            "abhanumber",
            "abhaaddress",
            "pan",
            "governmentid",
            "nationalid",
            "healthid",
            "आधार",
        }
    ),
}
_NAME_PARTS = {
    "firstname",
    "given",
    "givenname",
    "lastname",
    "family",
    "familyname",
    "surname",
}
_EXCLUDED_NAME_CONTEXTS = {
    "author",
    "contact",
    "doctor",
    "facility",
    "guardian",
    "practitioner",
    "relative",
    "staff",
}
_NOTE_LABELS: dict[str, tuple[str, ...]] = {
    "name": (
        "patient name",
        "full name",
        r"name",
        "रोगी का नाम",
        "मरीज का नाम",
        "नाम",
    ),
    "date_of_birth": ("date of birth", "birth date", "dob", "जन्म तिथि"),
    "age": ("age", "उम्र", "आयु"),
    "gender": ("gender", "sex", "लिंग"),
    "phone": ("phone number", "phone", "mobile", "contact number", "फोन", "मोबाइल"),
    "address": ("address", "पता"),
    "location": ("village", "district", "tehsil", "city", "location", "गाँव", "जिला"),
    "hospital_identifier": ("patient id", "uhid", "mrn", "hospital id"),
    "government_identifier": ("aadhaar", "aadhar", "abha number", "abha", "pan", "आधार"),
}
_NOTE_LABEL_ALTERNATIVES = sorted(
    (
        (label, field_name)
        for field_name, labels in _NOTE_LABELS.items()
        for label in labels
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)
_NOTE_PATTERN = re.compile(
    r"(?im)^\s*(?P<label>"
    + "|".join(
        r"\s+".join(re.escape(part) for part in label.split())
        for label, _ in _NOTE_LABEL_ALTERNATIVES
    )
    + r")\s*[:：=\-]\s*(?P<value>[^\r\n]+)"
)
_NOTE_LABEL_LOOKUP = {
    re.sub(r"[^\w]+", "", label.casefold()): field_name
    for label, field_name in _NOTE_LABEL_ALTERNATIVES
}

IdentityInput: TypeAlias = Mapping[str, object] | str


def normalize_phone(value: str) -> str | None:
    """Return valid Indian phone numbers as +91 E.164, or None when invalid."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    ascii_number = _ascii_digits(normalized)
    if not ascii_number or any(
        not character.isdecimal() and character not in _PHONE_PUNCTUATION
        for character in ascii_number
    ):
        return None
    try:
        parsed = phonenumbers.parse(ascii_number, "IN")
    except NumberParseException:
        return None
    if parsed.country_code != 91 or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def normalize_date(value: str | date | datetime) -> str | None:
    """Return a calendar date as ISO, refusing ambiguous numeric dates."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = _ascii_digits(unicodedata.normalize("NFKC", value).strip())
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass

    for date_format in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, date_format).date()
        except ValueError:
            continue
        if date_format in {"%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"}:
            first, second = _date_components(text, date_format)
            if first <= 12 and second <= 12:
                return None
        elif date_format in {"%m/%d/%Y", "%m-%d-%Y"}:
            first, second = _date_components(text, date_format)
            if first <= 12 and second <= 12:
                return None
        return parsed.isoformat()
    return None


def normalize_age(value: str | int | float) -> str | None:
    """Return a plausible age in whole years as a canonical decimal string."""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = _AGE.fullmatch(text)
    if match is None:
        return None
    age = int(match.group(1))
    return str(age) if age <= 125 else None


def normalize_gender(value: str) -> str | None:
    """Map common sex/gender values to a small, explicit vocabulary."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    values = {
        "m": "male",
        "male": "male",
        "man": "male",
        "पुरुष": "male",
        "f": "female",
        "female": "female",
        "woman": "female",
        "महिला": "female",
        "other": "other",
        "non-binary": "other",
        "nonbinary": "other",
        "o": "other",
        "unknown": "unknown",
        "u": "unknown",
        "undifferentiated": "unknown",
    }
    return values.get(normalized)


def normalize_identifier(value: str) -> str | None:
    """Normalize identifier case and separators without changing its digits."""
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    if not normalized:
        return None
    ascii_digits = _ascii_digits(normalized)
    return _ID_PUNCTUATION.sub("", ascii_digits)


def normalize_location(value: str) -> str:
    """Normalize a location's script, case, whitespace, and punctuation."""
    text = transliterate_to_latin(unicodedata.normalize("NFKC", value)).casefold()
    return " ".join(part for part in _WORD_SEPARATORS.split(text) if part)


def extract_identity(
    record: IdentityInput,
    source_file: str,
    record_locator: str = "doc",
) -> PatientIdentity:
    """Project one parsed row/object or labeled note into identity-only values."""
    if isinstance(record, str):
        candidates = _extract_note_values(record)
    elif isinstance(record, Mapping):
        if record.get("resourceType") == "Patient":
            candidates = _extract_fhir_patient(record)
        else:
            candidates = _extract_mapping_values(record)
    else:
        raise TypeError("identity_record_type_invalid")

    return _build_identity(candidates, source_file, record_locator)


def extract_fhir_identities(
    resource: Mapping[str, object],
    source_file: str,
    record_locator: str = "doc",
) -> tuple[PatientIdentity, ...]:
    """Extract one identity per Patient resource in a FHIR R4 resource or Bundle."""
    if resource.get("resourceType") == "Patient":
        return (extract_identity(resource, source_file, record_locator),)
    if resource.get("resourceType") != "Bundle":
        return ()

    identities: list[PatientIdentity] = []
    entries = resource.get("entry")
    if not isinstance(entries, list):
        return ()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        nested_resource = entry.get("resource")
        if not isinstance(nested_resource, Mapping) or nested_resource.get("resourceType") != "Patient":
            continue
        identities.append(
            extract_identity(
                nested_resource,
                source_file,
                f"{record_locator.rstrip('/')}/entry/{index}/resource"
                if record_locator != "doc"
                else f"/entry/{index}/resource",
            )
        )
    return tuple(identities)


def _build_identity(
    candidates: Mapping[str, list[tuple[object, str]]],
    source_file: str,
    record_locator: str,
) -> PatientIdentity:
    normalizers = {
        "name": _normalize_name,
        "date_of_birth": _normalize_date_value,
        "age": _normalize_age_value,
        "gender": _normalize_gender_value,
        "phone": _normalize_phone_value,
        "address": _normalize_address_value,
        "location": _normalize_location_value,
        "hospital_identifier": _normalize_identifier_value,
        "government_identifier": _normalize_identifier_value,
    }
    values: dict[str, tuple[IdentityValue, ...]] = {}
    invalid: set[str] = set()
    for field_name in _FIELD_NAMES:
        field_values: list[IdentityValue] = []
        seen: set[tuple[str, str]] = set()
        for raw, source_key in candidates.get(field_name, []):
            original = _scalar_text(raw)
            if original is None or original.strip().casefold() in _MISSING_WORDS:
                continue
            normalized = normalizers[field_name](raw)
            if normalized is None:
                invalid.add(field_name)
            unique_key = (original, source_key)
            if unique_key not in seen:
                seen.add(unique_key)
                field_values.append(IdentityValue(original, normalized, source_key))
        values[field_name] = tuple(field_values)

    metadata = (
        ("schema", "patient-identity/1"),
        ("name_normalization", "indic-transliteration ITRANS; order and initials retained"),
        ("date_policy", "ISO output; ambiguous numeric dates are invalid unless source-specific parsing is added"),
    )
    return PatientIdentity(
        source_file=source_file,
        record_locator=record_locator,
        **values,
        missing_fields=frozenset(
            field_name for field_name, field_values in values.items() if not field_values
        ),
        invalid_fields=frozenset(invalid),
        normalization_metadata=metadata,
    )


def _extract_mapping_values(record: Mapping[str, object]) -> dict[str, list[tuple[object, str]]]:
    candidates = _empty_candidates()
    stack: list[tuple[object, tuple[str, ...], int]] = [(record, (), 0)]
    while stack:
        value, path, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str):
                    continue
                child_path = path + (key,)
                stack.append((child, child_path, depth + 1))
                if _key_name(key) in _ALIASES["address"] and isinstance(child, str):
                    _append(candidates, "address", child, ".".join(child_path))
            continue
        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                stack.append((child, path + (str(index),), depth + 1))
            continue
        if _scalar_text(value) is None:
            continue
        _collect_mapping_scalar(candidates, path, value)
    _append_combined_name_parts(candidates)
    return candidates


def _append_combined_name_parts(
    candidates: dict[str, list[tuple[object, str]]],
) -> None:
    grouped: dict[str, dict[str, list[tuple[str, str]]]] = {}
    given_parts = {"firstname", "given", "givenname"}
    family_parts = {"lastname", "family", "familyname", "surname"}
    for raw, source_key in candidates["name"]:
        parts = source_key.split(".")
        key = _key_name(parts[-1])
        role = "given" if key in given_parts else "family" if key in family_parts else None
        group_parts = parts[:-1]
        if key.isdecimal() and len(parts) > 1 and _key_name(parts[-2]) == "given":
            role = "given"
            group_parts = parts[:-2]
        if role is None:
            continue
        grouped.setdefault(".".join(group_parts), {"given": [], "family": []})[role].append(
            (source_key, str(raw).strip())
        )

    for group, parts in grouped.items():
        if not parts["given"] or not parts["family"]:
            continue
        given = " ".join(value for _, value in sorted(parts["given"]))
        family = " ".join(value for _, value in sorted(parts["family"]))
        _append(candidates, "name", f"{given} {family}", group or "name_parts")


def _collect_mapping_scalar(
    candidates: dict[str, list[tuple[object, str]]],
    path: tuple[str, ...],
    value: object,
) -> None:
    key = _key_name(path[-1])
    contexts = {_key_name(part) for part in path[:-1]}
    if contexts & _EXCLUDED_NAME_CONTEXTS:
        return

    field_name = next(
        (name for name, aliases in _ALIASES.items() if key in aliases),
        None,
    )
    if field_name == "name":
        _append(candidates, "name", value, ".".join(path))
    elif key in _NAME_PARTS and (
        "name" in contexts
        or not contexts
        or contexts & {"patient", "person", "beneficiary", "subject"}
    ):
        _append(candidates, "name", value, ".".join(path))
    elif "name" in contexts and key.isdecimal():
        _append(candidates, "name", value, ".".join(path))
    elif field_name in {
        "date_of_birth",
        "age",
        "gender",
        "phone",
        "address",
        "location",
        "hospital_identifier",
        "government_identifier",
    }:
        if field_name == "address" and contexts & {"contact", "telecom"}:
            return
        _append(candidates, field_name, value, ".".join(path))
    elif key in {"line", "street", "streetaddress", "house"} and "address" in contexts:
        _append(candidates, "address", value, ".".join(path))
    elif key.isdecimal() and "address" in contexts:
        _append(candidates, "address", value, ".".join(path))


def _extract_fhir_patient(patient: Mapping[str, object]) -> dict[str, list[tuple[object, str]]]:
    candidates = _empty_candidates()
    for index, name in enumerate(_as_mappings(patient.get("name"))):
        path = f"name/{index}"
        _append_if_scalar(candidates, "name", name.get("text"), path + "/text")
        given = name.get("given")
        given_values = [value for value in given if _scalar_text(value) is not None] if isinstance(given, list) else []
        family = name.get("family")
        parts = given_values + ([family] if _scalar_text(family) is not None else [])
        if parts:
            combined = " ".join(str(part).strip() for part in parts)
            _append(candidates, "name", combined, path)
        for part_index, part in enumerate(parts):
            _append(candidates, "name", part, f"{path}/part/{part_index}")

    for key, field_name in (("gender", "gender"), ("birthDate", "date_of_birth")):
        _append_if_scalar(candidates, field_name, patient.get(key), key)

    for identifier_index, identifier in enumerate(_as_mappings(patient.get("identifier"))):
        value = identifier.get("value")
        if _scalar_text(value) is None:
            continue
        system = str(identifier.get("system", "")).casefold()
        coding = identifier.get("type")
        type_text = ""
        if isinstance(coding, Mapping):
            type_text = str(coding.get("text", "")).casefold()
        target = (
            "government_identifier"
            if any(word in system + " " + type_text for word in ("aadhaar", "aadhar", "abha", "pan", "national", "government"))
            else "hospital_identifier"
        )
        _append(candidates, target, value, f"identifier/{identifier_index}/value")

    for index, telecom in enumerate(_as_mappings(patient.get("telecom"))):
        if str(telecom.get("system", "")).casefold() == "phone":
            _append_if_scalar(candidates, "phone", telecom.get("value"), f"telecom/{index}/value")

    for index, address in enumerate(_as_mappings(patient.get("address"))):
        parts: list[str] = []
        for key in ("line", "city", "district", "state", "postalCode", "country"):
            part = address.get(key)
            part_values = part if isinstance(part, list) else [part]
            for item in part_values:
                if _scalar_text(item) is not None:
                    parts.append(str(item).strip())
                    if key != "line":
                        _append(candidates, "location", item, f"address/{index}/{key}")
        if parts:
            _append(candidates, "address", ", ".join(parts), f"address/{index}")
    return candidates


def _extract_note_values(note: str) -> dict[str, list[tuple[object, str]]]:
    candidates = _empty_candidates()
    for match in _NOTE_PATTERN.finditer(note):
        label = _key_name(match.group("label"))
        field_name = _NOTE_LABEL_LOOKUP.get(label)
        if field_name is not None:
            _append(candidates, field_name, match.group("value").strip(), label)
    return candidates


def _normalize_name(value: object) -> str | None:
    text = _scalar_text(value)
    normalized = normalize_name(text or "")
    return normalized or None


def _normalize_date_value(value: object) -> str | None:
    if isinstance(value, (date, datetime)):
        return normalize_date(value)
    text = _scalar_text(value)
    return normalize_date(text) if text is not None else None


def _normalize_age_value(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    return normalize_age(value) if isinstance(value, (str, int, float)) else None


def _normalize_gender_value(value: object) -> str | None:
    text = _scalar_text(value)
    return normalize_gender(text) if text is not None else None


def _normalize_phone_value(value: object) -> str | None:
    text = _scalar_text(value)
    return normalize_phone(text) if text is not None else None


def _normalize_address_value(value: object) -> str | None:
    text = _scalar_text(value)
    normalized = normalize_location(text or "")
    return normalized or None


def _normalize_location_value(value: object) -> str | None:
    return _normalize_address_value(value)


def _normalize_identifier_value(value: object) -> str | None:
    text = _scalar_text(value)
    return normalize_identifier(text) if text is not None else None


def _append_if_scalar(
    candidates: dict[str, list[tuple[object, str]]],
    field_name: str,
    value: object,
    source_key: str,
) -> None:
    if _scalar_text(value) is not None:
        _append(candidates, field_name, value, source_key)


def _append(
    candidates: dict[str, list[tuple[object, str]]],
    field_name: str,
    value: object,
    source_key: str,
) -> None:
    candidates[field_name].append((value, source_key))


def _empty_candidates() -> dict[str, list[tuple[object, str]]]:
    return {field_name: [] for field_name in _FIELD_NAMES}


def _scalar_text(value: object) -> str | None:
    if isinstance(value, bool) or value is None or isinstance(value, (Mapping, list, tuple)):
        return None
    if not isinstance(value, (str, int, float, date, datetime)):
        return None
    text = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return text if text.strip() else None


def _key_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in text if character.isalnum())


def _ascii_digits(value: str) -> str:
    return "".join(
        str(unicodedata.digit(character)) if character.isdecimal() else character
        for character in value
    )


def _as_mappings(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _date_components(value: str, date_format: str) -> tuple[int, int]:
    separator = "/" if "/" in date_format else "-" if "-" in date_format else "."
    first, second, _ = value.split(separator)
    return int(first), int(second)


__all__ = [
    "extract_fhir_identities",
    "extract_identity",
    "normalize_age",
    "normalize_date",
    "normalize_gender",
    "normalize_identifier",
    "normalize_location",
    "normalize_phone",
]
