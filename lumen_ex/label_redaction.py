"""Redact patient and relative names signaled by labels or honorifics."""

import re


_PATIENT_NAME = re.compile(
    r"(?im)(?P<label>^[ \t]*|[|;,][ \t]*)"
    r"(?P<field>patient(?:[ \t]+name)?|full[ \t]+name|name|"
    r"patient's[ \t]+name|naam|रोगी का नाम|मरीज का नाम|नाम)[ \t]*[:：][ \t]*"
    r"(?P<value>[^\r\n,;|]*?\S)(?=[ \t]*(?:[,;|\r\n]|$))"
)

_RELATIVE_LABEL = re.compile(
    r"(?im)(?P<label>^[ \t]*|[|;,][ \t]*)"
    r"(?:attendant|guardian|relative(?:[ \t]+name)?|"
    r"पिता का नाम|पति का नाम|माता का नाम)[ \t]*[:：][ \t]*"
    r"(?P<value>[^\r\n,;|]*?\S)(?=[ \t]*(?:[,;|\r\n]|$))"
)

# Require a name-like token beside a bare honorific. The honorific is itself
# case-insensitive, while a Latin name starts with an uppercase letter.
_NAME_WORD = r"(?:[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)*|[\u0904-\u0939][\u0900-\u0963]*)"
_NAME_WORDS = rf"{_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{0,2}}"
_HONORIFIC_NAME = re.compile(
    rf"(?<![\w.])(?P<value>{_NAME_WORDS}[ \t]+"
    r"(?i:ji|sahab|saab|didi|behenji|babu|bhaiya|bhai|amma|chachu|mama|papa|"
    r"जी|साहब|दीदी|भैया|अम्मा))(?!\w)"
)

# C/O also means complaint of. Handle it only in a labeled contact/address
# context or where a title-cased name is distinguishable from a clinical phrase.
_RELATION_NAME = re.compile(
    rf"(?<![\w/])(?i:[SDW]/O)[ \t]+(?P<value>{_NAME_WORDS})"
)
_CARE_OF = re.compile(
    rf"(?<![\w/])(?i:C/O)[ \t]+(?P<value>{_NAME_WORDS})(?![\w'-])"
)
_CONTACT_CARE_OF = re.compile(r"(?i)\b(?:address|attendant|contact|guardian|relative)\b[^\r\n]{0,30}$")
_CLINICAL_TERM = re.compile(
    r"(?i)^(?:fever|cough|pain|breathlessness|headache|vomiting|weakness|dizziness|"
    r"nausea|chest|abdominal|sore|swelling|rash|shortness|back|joint|difficulty|"
    r"diarrh?oea|diarrhea|fatigue|palpitation|bleeding|breathing|burning|dysuria|"
    r"asthma|diabetes|hypertension|dengue|tuberculosis|covid|बुखार|खांसी|दर्द)\b"
)
_CLINICIAN_TITLE = re.compile(r"(?i)\b(?:dr|doctor|prof)\.?[ \t]+$")
_STARTS_WITH_CLINICIAN_TITLE = re.compile(r"(?i)^(?:dr|doctor|prof)\.?[ \t]+")
_CLINICAL_CONTEXT = re.compile(
    r"(?i)^[ \t]+(?:for|since|with|and|palsy|disease|syndrome|infection)\b"
)


def redact_labeled_names(text: str) -> tuple[str, int]:
    """Preserve field labels, clinician names, and adjacent clinical fields."""
    count = 0
    for pattern in (_PATIENT_NAME, _RELATIVE_LABEL, _RELATION_NAME, _CARE_OF, _HONORIFIC_NAME):
        def replace(match):
            nonlocal count
            if pattern is _HONORIFIC_NAME:
                prefix = text[max(0, match.start() - 24):match.start()]
                if _CLINICIAN_TITLE.search(prefix) or _STARTS_WITH_CLINICIAN_TITLE.match(match["value"]):
                    return match.group()
            if pattern is _CARE_OF:
                prefix = text[max(0, match.start() - 48):match.start()]
                if not _CONTACT_CARE_OF.search(prefix) and (
                    _CLINICAL_TERM.match(match["value"]) or _CLINICAL_CONTEXT.match(text[match.end():match.end() + 32])
                ):
                    return match.group()
            count += 1
            return text[match.start():match.start("value")] + "[NAME]"

        text = pattern.sub(replace, text)
    return text, count


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
