"""Redact explicitly labeled patient names in multilingual notes."""

import re


_PATIENT_NAME = re.compile(
    r"(?im)(?P<label>^\s*|[|;]\s*)"
    r"(?P<field>patient(?:[ \t]+name)?|full[ \t]+name|name|"
    r"रोगी का नाम|मरीज का नाम|नाम)[ \t]*[:：][ \t]*"
    r"(?P<value>[^\r\n,;|]*?\S)(?=[ \t]*(?:[,;|\r\n]|$))"
)


def redact_labeled_names(text: str) -> tuple[str, int]:
    """Preserve field labels and adjacent delimited clinical fields."""
    return _PATIENT_NAME.subn(
        lambda match: text[match.start():match.start("value")] + "[NAME]",
        text,
    )


_PRIVATE_COLUMNS = frozenset({
    "aadhaar", "aadhaarno", "aadhar", "abha", "abhano", "address",
    "dob", "dobmmddyyyy", "email", "firstname", "guardian",
    "janmtithi", "lastname", "mobileno", "mobile", "naam", "pan",
    "mrn", "patientid", "patientname", "payerspan", "payerpan", "phone",
    "pin", "pincode", "pitapatikanaam", "pmjayid", "policyno", "regno",
    "relativename", "street", "uhid", "villagemohalla", "gaon", "pata", "zip",
})


def is_private_column(header: str) -> bool:
    """Recognize explicit private fields without guessing from cell values."""
    return re.sub(r"[^a-z0-9]", "", header.casefold()) in _PRIVATE_COLUMNS
