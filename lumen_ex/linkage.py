"""Conservative, release-local linkage for parsed patient records."""

from collections import defaultdict
from itertools import product
import re

from lumen_ex.identity import extract_fhir_identities, extract_identity


_HOSPITAL_ID = re.compile(r"^[A-Z]{2,8}[A-Z0-9]*\d{4,}$")
_GOVERNMENT_ID = re.compile(r"^(?:\d{12}|\d{14}|[A-Z]{5}\d{4}[A-Z])$")


def _values(identity, field):
    return {value.normalized for value in getattr(identity, field) if value.normalized}


def _government_ids(identity):
    return {
        (len(value), value)
        for value in _values(identity, "government_identifier")
        if _GOVERNMENT_ID.fullmatch(value)
    }


def _evidence(identity):
    for _, value in _government_ids(identity):
        yield ("government", value)
    for value in _values(identity, "hospital_identifier"):
        if _HOSPITAL_ID.fullmatch(value):
            yield ("hospital", value)
    for field in ("phone", "date_of_birth"):
        for name, value in product(_values(identity, "name"), _values(identity, field)):
            if len(name.split()) >= 2:
                yield (field, name, value)


def build_link_index(records):
    """Return stable cluster keys for records supported by shared identity evidence."""
    buckets = defaultdict(list)
    conflicts = {}
    for file, locator, record in records:
        if isinstance(record, dict) and record.get("resourceType") == "Bundle":
            patients = extract_fhir_identities(record, file)
            if len(patients) != 1:
                continue
            identity = patients[0]
        else:
            identity = extract_identity(record, file, locator)
        key = (file, locator)
        conflicts[key] = _government_ids(identity)
        for evidence in _evidence(identity):
            buckets[evidence].append(key)

    groups = {}
    membership = {}
    for candidates in (buckets[key] for key in sorted(buckets)):
        if len(candidates) < 2:
            continue
        roots = {membership.get(key, key) for key in candidates}
        members = set().union(*(groups.get(root, {root}) for root in roots))
        ids_by_kind = defaultdict(set)
        for kind, value in set().union(*(conflicts[key] for key in members)):
            ids_by_kind[kind].add(value)
        if any(len(values) > 1 for values in ids_by_kind.values()):
            continue
        root = min(roots)
        for old_root in roots:
            groups.pop(old_root, None)
        groups[root] = members
        for member in members:
            membership[member] = root

    return {
        member: repr(root)
        for member, root in membership.items()
    }
