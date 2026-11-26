"""Sanitize identifiers carried by paths, schema labels, and attachments."""

import base64
import binascii
import hashlib
import hmac
from pathlib import PurePosixPath
import re
from uuid import UUID


_RECORD_ID = re.compile(
    r"(?<![^\W_])[A-Z]{2,8}(?:[-/ ]\d{1,4}){0,2}[-/ ]?\d{5,}(?!\d)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<!\d)\d{10,}(?!\d)")
_NAMED_FILE = re.compile(
    r"(?:discharge[ _-]+summary|patient[ _-]+(?:record|report|note))"
    r"[ _-]+[^_\W]+[ _-]+[^_\W]+",
    re.IGNORECASE,
)
_DYNAMIC_PII_MAPS = {
    "byname", "byfullname", "bypatientname", "bypatientid", "byuhid", "bymrn",
    "byphone", "byemail", "bynationalid",
}
_SOURCE_PATH_FIELDS = {
    "sourcepath", "sourcefilepath", "originalpath", "originalfilename", "sourcefilename",
    "exportpath", "filepath",
}
_ATTACHMENT_NAME = re.compile(
    r"(?im)(?P<label>^\s*(?:referral\s*:|consent witnessed by\s+))"
    r"(?P<name>[^\r\n,;(]+?)\s*(?=[(,;\r\n]|$)"
)


def field_key(label):
    return re.sub(r"[^a-z0-9]", "", label.casefold())


class HiddenChannels:
    def __init__(self, secret):
        self.secret = secret

    def token(self, namespace, value):
        message = f"{namespace}\0{value}".encode("utf-8")
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()[:24]

    def file_path(self, path, cleaner):
        parts = []
        for component in path.parts:
            identifier = (
                _RECORD_ID.search(component)
                or _NUMBER.search(component)
                or _NAMED_FILE.search(component)
                or cleaner(component) != component
            )
            if identifier:
                suffix = PurePosixPath(component).suffix
                component = "export_" + self.token("path", component) + suffix
            parts.append(component)
        return "/".join(parts)

    def header(self, label, cleaner):
        if _RECORD_ID.search(label) or _NUMBER.search(label) or cleaner(label) != label:
            return "column_" + self.token("header", label)
        return label

    def key(self, label, parent, cleaner):
        if field_key(parent) in _DYNAMIC_PII_MAPS:
            return "entry_" + self.token("key", label)
        return self.header(label, cleaner)

    def value(self, label, value, parent, cleaner, parent_label):
        if field_key(parent_label) in _DYNAMIC_PII_MAPS and _RECORD_ID.search(value):
            return "entry_" + self.token("key", value)
        if field_key(label) in _SOURCE_PATH_FIELDS:
            return "[SOURCE REDACTED]" if value else value
        if label == "source" and (
            value.startswith("#") or _RECORD_ID.search(value) or "\\" in value
        ):
            return "urn:raksha:source:" + self.token("source", value)
        if label == "data" and isinstance(parent.get("contentType"), str):
            return self.attachment(value, parent["contentType"], cleaner)
        if field_key(label) in {"base64", "attachmentbase64", "encodeddata"}:
            return self.attachment(value, "text/plain", cleaner)
        return None

    @staticmethod
    def attachment(value, content_type, cleaner):
        """Keep base64 text structure and fail closed on uninspectable payloads."""
        try:
            decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
            if not content_type.casefold().startswith("text/plain"):
                raise ValueError("attachment_format_unsupported")
            original_text = decoded.decode("utf-8")
            text = original_text
            text = _ATTACHMENT_NAME.sub(r"\g<label>[NAME] ", text)
            text = _RECORD_ID.sub("[IDENTIFIER]", text)
            cleaned = cleaner(text)
            if cleaned == original_text:
                raise ValueError("attachment_content_unrecognized")
        except (binascii.Error, ValueError):
            # The caller quarantines the containing file rather than emitting a
            # placeholder document with an incorrect FHIR attachment MIME type.
            raise ValueError("attachment_not_safely_sanitized") from None
        return base64.b64encode(cleaned.encode("utf-8")).decode("ascii")


    def scrub_fhir(self, root, cleaner):
        """Remove Patient details repeated in FHIR display fields and narratives."""
        if not isinstance(root, dict) or root.get("resourceType") not in {"Bundle", "Patient"}:
            return 0

        entries = root.get("entry")
        resources = [root]
        full_urls = {}
        if isinstance(entries, list):
            for entry in entries:
                resource = entry.get("resource") if isinstance(entry, dict) else None
                if isinstance(resource, dict):
                    resources.append(resource)
                    if isinstance(entry.get("fullUrl"), str):
                        full_urls[entry["fullUrl"]] = resource

        sensitive = set()
        count = 0
        for resource in resources:
            if resource.get("resourceType") != "Patient":
                continue
            names = resource.get("name")
            if isinstance(names, list):
                for name in names:
                    if not isinstance(name, dict):
                        continue
                    if isinstance(name.get("text"), str):
                        sensitive.add(name["text"])
                        name["text"] = "[NAME]"
                        count += 1
                    given_names = name.get("given")
                    family = name.get("family")
                    if isinstance(given_names, list):
                        given = [part for part in given_names if isinstance(part, str)]
                        if given and isinstance(family, str):
                            sensitive.add(" ".join([*given, family]))
                        name["given"] = ["[NAME]"] * len(given_names)
                        count += len(given_names)
                    if isinstance(family, str):
                        name["family"] = "[NAME]"
                        count += 1
            identifiers = resource.get("identifier")
            if isinstance(identifiers, list):
                for identifier in identifiers:
                    value = identifier.get("value") if isinstance(identifier, dict) else None
                    if isinstance(value, str) and value and not value.startswith("PSN-"):
                        sensitive.add(value)
                        identifier["value"] = "id-" + self.token("fhir-identifier", value)
                        count += 1

        parts = sorted((part for part in sensitive if len(part.strip()) >= 3), key=lambda part: (-len(part), part))
        pattern = (
            re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, parts)) + r")(?!\w)", re.IGNORECASE)
            if parts else None
        )

        references = {}
        for resource in resources:
            original = resource.get("id")
            resource_type = resource.get("resourceType")
            if not isinstance(original, str) or not isinstance(resource_type, str):
                continue
            if cleaner(original) == original and not (pattern and pattern.search(original)):
                continue
            new_id = "r-" + self.token("fhir-resource", f"{resource_type}/{original}")
            references[f"{resource_type}/{original}"] = f"{resource_type}/{new_id}"
            resource["id"] = new_id
            count += 1
        for full_url in full_urls:
            if cleaner(full_url) != full_url or (pattern and pattern.search(full_url)):
                uid = self.token("fhir-url", full_url).ljust(32, "0")
                references[full_url] = f"urn:uuid:{UUID(hex=uid)}"

        def scrub(node):
            nonlocal count
            if isinstance(node, list):
                for item in node:
                    scrub(item)
            if not isinstance(node, dict):
                return
            for key, value in node.items():
                if not isinstance(value, str):
                    scrub(value)
                    continue
                if key in {"reference", "fullUrl"}:
                    if value in references:
                        node[key] = references[value]
                        count += 1
                    continue
                if pattern and key not in {"data", "id", "resourceType", "system", "url"}:
                    node[key], replacements = pattern.subn("[REDACTED]", value)
                    count += replacements

        scrub(root)
        return count
