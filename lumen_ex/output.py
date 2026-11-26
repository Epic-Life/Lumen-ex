"""Contract-safe serialization and validation for sanitized release artifacts."""

import copy
import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from lumen_ex.contracts.models import (
    InputFileMetadata,
    InputFormat,
    NormalizedRecord,
    OutputManifestEntry,
    ParsedRecord,
    ProcessingStatus,
    SanitizationResult,
)
from lumen_ex.errors import ProcessingError, SAFE_ERROR_CODES
from lumen_ex.ingestion import validate_fhir_document
from lumen_ex.security import SensitiveDataLeakScanner


@dataclass(frozen=True)
class ReleaseRecord:
    parsed: ParsedRecord
    normalized: NormalizedRecord
    result: SanitizationResult


@dataclass(frozen=True)
class FileWriteResult:
    manifest: OutputManifestEntry
    records: tuple[dict[str, object], ...] = ()
    records_quarantined: int = 0


class SanitizedOutputWriter:
    """Serialize complete source files only after all of their records pass checks."""

    def __init__(
        self,
        input_root: Path,
        output_root: Path,
        leak_scanner: SensitiveDataLeakScanner,
    ) -> None:
        self.input_root = input_root
        self.output_root = output_root
        self._scanner = leak_scanner
        self._claimed_paths: set[str] = set()

    def write_file(
        self,
        source: InputFileMetadata,
        records: Sequence[ReleaseRecord],
    ) -> FileWriteResult:
        if any(item.result.status is ProcessingStatus.QUARANTINED for item in records):
            reason = next(
                item.result.error_code
                for item in records
                if item.result.status is ProcessingStatus.QUARANTINED
            )
            return self._quarantine(source, reason or "internal_error", len(records))

        sanitized = [
            item for item in records
            if item.result.status is ProcessingStatus.SANITIZED
        ]
        if records and not sanitized:
            return FileWriteResult(
                OutputManifestEntry(
                    source.sha256, None, ProcessingStatus.SUPPRESSED
                )
            )

        try:
            output_path = self._output_path(source, records)
            content, entries = self._serialize(source, records, output_path)
            if not self._scanner.scan_artifact_text(
                content, filename=output_path
            ) or not all(self._scanner.scan_value(entry) for entry in entries):
                return self._quarantine(source, "leak_scan_failed", len(records))
            destination = self._destination(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._assert_no_symlink(destination)
            created = False
            try:
                with destination.open(
                    "x", encoding="utf-8", newline=""
                ) as stream:
                    created = True
                    stream.write(content)
            except OSError:
                if created and destination.is_file() and not destination.is_symlink():
                    destination.unlink(missing_ok=True)
                raise
        except ProcessingError as error:
            return self._quarantine(source, error.code, len(records))
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError, csv.Error):
            return self._quarantine(source, "internal_error", len(records))

        return FileWriteResult(
            OutputManifestEntry(
                source.sha256, output_path, ProcessingStatus.SANITIZED
            ),
            tuple(entries),
        )

    def _output_path(
        self, source: InputFileMetadata, records: Sequence[ReleaseRecord]
    ) -> str:
        paths = {
            item.result.output_relative_path
            for item in records
            if item.result.output_relative_path is not None
        }
        if len(paths) > 1:
            source_path = "sanitized/" + source.relative_path.replace("\\", "/")
            explicit_paths = {path for path in paths if path != source_path}
            if len(explicit_paths) != 1:
                raise ProcessingError("invalid_record")
            candidate = next(iter(explicit_paths))
        elif paths:
            candidate = next(iter(paths))
        else:
            candidate = "sanitized/" + source.relative_path.replace("\\", "/")
        if not _safe_output_path(candidate):
            raise ProcessingError("invalid_record")
        if candidate in self._claimed_paths:
            path = PurePosixPath(candidate)
            relative_digest = hashlib.sha256(
                source.relative_path.encode("utf-8")
            ).hexdigest()[:10]
            candidate = str(
                path.with_name(
                    f"file_{source.sha256[:16]}_{relative_digest}{path.suffix}"
                )
            )
            if candidate in self._claimed_paths:
                raise ProcessingError("filesystem_error")
        self._claimed_paths.add(candidate)
        return candidate

    def _serialize(
        self,
        source: InputFileMetadata,
        records: Sequence[ReleaseRecord],
        output_path: str,
    ) -> tuple[str, list[dict[str, object]]]:
        path = self.input_root.joinpath(*source.relative_path.replace("\\", "/").split("/"))
        if path.is_symlink():
            raise ProcessingError("unsafe_symlink")
        try:
            path.resolve(strict=True).relative_to(
                self.input_root.resolve(strict=True)
            )
        except (OSError, ValueError):
            raise ProcessingError("unsafe_symlink") from None
        raw = path.read_bytes()
        if (
            len(raw) != source.size_bytes
            or hashlib.sha256(raw).hexdigest() != source.sha256
        ):
            raise ProcessingError("source_changed")
        try:
            text = raw.decode(source.encoding or "utf-8-sig")
        except UnicodeError:
            raise ProcessingError("undecodable_text") from None

        extension = PurePosixPath(source.relative_path).suffix.casefold()
        if extension in {".csv", ".tsv"}:
            return self._serialize_delimited(
                records, output_path, text, "\t" if extension == ".tsv" else ","
            )
        if extension == ".txt":
            return self._serialize_text(records, output_path)
        if extension == ".json":
            return self._serialize_json(source, records, output_path, text)
        raise ProcessingError("unsupported_format")

    def _serialize_delimited(
        self,
        records: Sequence[ReleaseRecord],
        output_path: str,
        text: str,
        delimiter: str,
    ) -> tuple[str, list[dict[str, object]]]:
        original_rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        headers = next(original_rows, None)
        if not headers:
            raise ProcessingError("empty_file")
        if len(set(headers)) != len(headers):
            raise ProcessingError("format_mismatch")
        output_headers = headers
        for item in records:
            result_headers = item.result.output_structure.get("headers")
            if isinstance(result_headers, (list, tuple)):
                output_headers = list(result_headers)
                break
        if any(not isinstance(header, str) for header in output_headers):
            raise ProcessingError("invalid_record")
        if len(set(output_headers)) != len(output_headers):
            raise ProcessingError("format_mismatch")

        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
        writer.writerow(output_headers)
        entries: list[dict[str, object]] = []
        output_row = 0
        for item in records:
            if item.result.status is ProcessingStatus.SUPPRESSED:
                continue
            fields = copy.deepcopy(item.result.output_fields)
            patient = _inject_patient_reference(item, fields)
            row = [fields.get(header, "") for header in output_headers]
            overflow = fields.get("None")
            if isinstance(overflow, list):
                row.extend(overflow)
            if len(row) != len(output_headers) and not isinstance(overflow, list):
                raise ProcessingError("invalid_record")
            writer.writerow(row)
            input_locator = item.parsed.locator
            output_locator = f"row:{output_row}"
            entries.append(
                _record_entry(
                    output_path, input_locator, output_locator, patient
                )
            )
            output_row += 1
        return stream.getvalue(), entries

    def _serialize_text(
        self, records: Sequence[ReleaseRecord], output_path: str
    ) -> tuple[str, list[dict[str, object]]]:
        if len(records) != 1 or records[0].result.status is ProcessingStatus.SUPPRESSED:
            raise ProcessingError("invalid_record")
        item = records[0]
        fields = copy.deepcopy(item.result.output_fields)
        text = fields.get("text")
        if not isinstance(text, str):
            raise ProcessingError("invalid_record")
        patient = item.result.patient_pseudonym
        if patient is not None:
            text = f"Patient: {patient}\n{text}"
        return text, [
            _record_entry(output_path, item.parsed.locator, item.parsed.locator, patient)
        ]

    def _serialize_json(
        self,
        source: InputFileMetadata,
        records: Sequence[ReleaseRecord],
        output_path: str,
        text: str,
    ) -> tuple[str, list[dict[str, object]]]:
        try:
            document = json.loads(text, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, RecursionError):
            raise ProcessingError("JSONDecodeError") from None
        except ValueError:
            raise ProcessingError("format_mismatch") from None
        if source.detected_format is InputFormat.FHIR:
            validate_fhir_document(document)
        entries: list[dict[str, object]] = []
        if not records:
            return json.dumps(document, ensure_ascii=False, indent=2) + "\n", entries

        if len(records) == 1 and records[0].parsed.locator == "doc":
            item = records[0]
            if item.result.status is ProcessingStatus.SUPPRESSED:
                raise ProcessingError("invalid_record")
            replacement = copy.deepcopy(item.result.output_fields)
            patient = _inject_patient_reference(item, replacement)
            document = replacement
            entries.append(
                _record_entry(output_path, "doc", "doc", patient)
            )
            return json.dumps(document, ensure_ascii=False, indent=2) + "\n", entries

        pointers: list[tuple[tuple[str, ...], ReleaseRecord, object, str | None]] = []
        for item in records:
            segments = _parse_pointer(item.parsed.locator)
            _get_pointer(document, segments)
            replacement: object = None
            patient: str | None = None
            if item.result.status is ProcessingStatus.SANITIZED:
                replacement = copy.deepcopy(item.result.output_fields)
                patient = _inject_patient_reference(item, replacement)
            pointers.append((segments, item, replacement, patient))

        output_locators: dict[str, str] = {}
        for segments, item, _, _ in pointers:
            parent = segments[:-1]
            index = _array_index(segments[-1])
            removed_before = sum(
                1
                for other_segments, other, _, _ in pointers
                if other.result.status is ProcessingStatus.SUPPRESSED
                and other_segments[:-1] == parent
                and _array_index(other_segments[-1]) < index
            )
            output_segments = (*parent, str(index - removed_before))
            output_locators[item.parsed.locator] = _format_pointer(output_segments)

        for segments, item, replacement, _ in sorted(
            pointers,
            key=lambda record: tuple(
                (0, int(segment)) if segment.isdecimal() else (1, segment)
                for segment in record[0]
            ),
            reverse=True,
        ):
            if item.result.status is ProcessingStatus.SUPPRESSED:
                _remove_pointer(document, segments)
            else:
                _set_pointer(document, segments, replacement)

        for _, item, _, patient in pointers:
            if item.result.status is ProcessingStatus.SANITIZED:
                entries.append(
                    _record_entry(
                        output_path,
                        item.parsed.locator,
                        output_locators[item.parsed.locator],
                        patient,
                    )
                )
        return json.dumps(document, ensure_ascii=False, indent=2) + "\n", entries

    def _destination(self, output_path: str) -> Path:
        destination = self.output_root.joinpath(*output_path.split("/"))
        try:
            destination.resolve().relative_to(self.output_root.resolve())
        except (OSError, ValueError):
            raise ProcessingError("invalid_record") from None
        return destination

    @staticmethod
    def _assert_no_symlink(destination: Path) -> None:
        current = destination
        while current != current.parent:
            if current.is_symlink():
                raise ProcessingError("unsafe_symlink")
            current = current.parent

    @staticmethod
    def _quarantine(
        source: InputFileMetadata, reason: str, record_count: int
    ) -> FileWriteResult:
        return FileWriteResult(
            OutputManifestEntry(
                source.sha256,
                None,
                ProcessingStatus.QUARANTINED,
                reason if reason in SAFE_ERROR_CODES else "internal_error",
            ),
            records_quarantined=record_count,
        )


class OutputContractValidator:
    """Check manifest counts, safe paths, and record-to-artifact pseudonym consistency."""

    def validate(
        self,
        output_root: Path,
        manifest: Sequence[OutputManifestEntry],
        records: Sequence[dict[str, object]],
        report: Mapping[str, object],
    ) -> None:
        if report.get("schema") != "raksha-report/1":
            raise ProcessingError("invalid_record")
        counts = report.get("counts")
        if not isinstance(counts, Mapping):
            raise ProcessingError("invalid_record")
        if counts.get("files_total") != len(manifest):
            raise ProcessingError("invalid_record")
        if counts.get("files_sanitized") != sum(
            entry.status is ProcessingStatus.SANITIZED for entry in manifest
        ):
            raise ProcessingError("invalid_record")
        if counts.get("files_quarantined") != sum(
            entry.status is ProcessingStatus.QUARANTINED for entry in manifest
        ):
            raise ProcessingError("invalid_record")
        if counts.get("records_processed") != len(records):
            raise ProcessingError("invalid_record")

        exported = {
            entry.output
            for entry in manifest
            if entry.status is ProcessingStatus.SANITIZED
        }
        seen_records: set[tuple[str, str]] = set()
        for record in records:
            output = record.get("file")
            if not isinstance(output, str) or output not in exported:
                raise ProcessingError("invalid_record")
            locator = record.get("loc")
            if (
                not isinstance(locator, str)
                or not locator
                or "patient" not in record
                or record["patient"] is not None
                and not isinstance(record["patient"], str)
            ):
                raise ProcessingError("invalid_record")
            record_key = (output, locator)
            if record_key in seen_records:
                raise ProcessingError("invalid_record")
            seen_records.add(record_key)
            path = output_root.joinpath(*output.split("/"))
            if path.is_symlink() or not path.is_file():
                raise ProcessingError("invalid_record")
            try:
                path.resolve().relative_to(output_root.resolve())
            except (OSError, ValueError):
                raise ProcessingError("invalid_record") from None
            patient = record.get("patient")
            if patient is not None and (
                not isinstance(patient, str)
                or not _record_contains_patient(path, record, patient)
            ):
                raise ProcessingError("invalid_record")


def _record_entry(
    output_path: str, input_locator: str, output_locator: str, patient: str | None
) -> dict[str, object]:
    entry: dict[str, object] = {
        "file": output_path,
        "loc": input_locator,
        "patient": patient,
    }
    if output_locator != input_locator:
        entry["out"] = output_locator
    return entry


def _inject_patient_reference(item: ReleaseRecord, fields: object) -> str | None:
    patient = item.result.patient_pseudonym
    if patient is None:
        return None
    normalized_fields = getattr(item.normalized, "fields", None)
    identities = getattr(item.normalized, "identities", ())
    if not isinstance(normalized_fields, Mapping) or len(identities) != 1:
        return None
    identity = identities[0]
    for field_name in (
        "government_identifier",
        "insurance_identifier",
        "hospital_identifier",
        "phone",
        "email",
        "name",
        "relative_name",
    ):
        for value in getattr(identity, field_name, ()):
            segments = _identity_path(value.source_key)
            if (
                item.parsed.source.detected_format is InputFormat.FHIR
                and len(identities) == 1
            ):
                patient_locator = getattr(identity, "record_locator", "")
                if isinstance(patient_locator, str) and patient_locator.startswith("/"):
                    segments = (*_parse_pointer(patient_locator), *segments)
            if segments and _set_corresponding_value(
                normalized_fields, fields, segments, patient
            ):
                return patient
    return None


def _identity_path(source_key: str) -> tuple[str, ...]:
    if not source_key or source_key == "name_parts":
        return ()
    delimiter = "/" if "/" in source_key else "."
    return tuple(part for part in source_key.split(delimiter) if part)


def _set_corresponding_value(
    original: object, transformed: object, segments: tuple[str, ...], value: str
) -> bool:
    if not segments:
        return False
    if isinstance(original, Mapping) and isinstance(transformed, dict):
        key: object | None = None
        remaining = segments
        if ".".join(segments) in original:
            key = ".".join(segments)
            remaining = ()
        elif segments[0] in original:
            key = segments[0]
            remaining = segments[1:]
        if key is None:
            return False
        original_keys = list(original)
        transformed_keys = list(transformed)
        try:
            transformed_key = transformed_keys[original_keys.index(key)]
        except (ValueError, IndexError):
            return False
        if not remaining:
            if isinstance(original[key], str) and isinstance(transformed.get(transformed_key), str):
                transformed[transformed_key] = value
                return True
            return False
        return _set_corresponding_value(
            original[key], transformed.get(transformed_key), remaining, value
        )
    if isinstance(original, list) and isinstance(transformed, list):
        try:
            index = int(segments[0])
        except ValueError:
            return False
        if index < 0 or index >= len(original) or index >= len(transformed):
            return False
        if len(segments) == 1:
            if isinstance(original[index], str) and isinstance(transformed[index], str):
                transformed[index] = value
                return True
            return False
        return _set_corresponding_value(
            original[index], transformed[index], segments[1:], value
        )
    return False


def _safe_output_path(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("sanitized/"):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _parse_pointer(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/"):
        raise ProcessingError("invalid_record")
    return tuple(
        segment.replace("~1", "/").replace("~0", "~")
        for segment in pointer[1:].split("/")
    )


def _format_pointer(segments: Sequence[str]) -> str:
    return "/" + "/".join(
        segment.replace("~", "~0").replace("/", "~1") for segment in segments
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _array_index(segment: str) -> int:
    if not segment.isdecimal():
        raise ProcessingError("invalid_record")
    return int(segment)


def _get_pointer(document: object, segments: Sequence[str]) -> object:
    current = document
    for segment in segments:
        if isinstance(current, list):
            index = _array_index(segment)
            if index >= len(current):
                raise ProcessingError("invalid_record")
            current = current[index]
        elif isinstance(current, Mapping) and segment in current:
            current = current[segment]
        else:
            raise ProcessingError("invalid_record")
    return current


def _set_pointer(document: object, segments: Sequence[str], value: object) -> None:
    if not segments:
        raise ProcessingError("invalid_record")
    parent = _get_pointer(document, segments[:-1])
    segment = segments[-1]
    if isinstance(parent, list):
        index = _array_index(segment)
        if index >= len(parent):
            raise ProcessingError("invalid_record")
        parent[index] = value
    elif isinstance(parent, dict) and segment in parent:
        parent[segment] = value
    else:
        raise ProcessingError("invalid_record")


def _remove_pointer(document: object, segments: Sequence[str]) -> None:
    if not segments:
        raise ProcessingError("invalid_record")
    parent = _get_pointer(document, segments[:-1])
    segment = segments[-1]
    if isinstance(parent, list):
        index = _array_index(segment)
        if index >= len(parent):
            raise ProcessingError("invalid_record")
        del parent[index]
    elif isinstance(parent, dict) and segment in parent:
        del parent[segment]
    else:
        raise ProcessingError("invalid_record")


def _record_contains_patient(path: Path, record: Mapping[str, object], patient: str) -> bool:
    locator = record.get("out", record.get("loc"))
    suffix = path.suffix.casefold()
    try:
        if suffix in {".csv", ".tsv"}:
            with path.open("r", encoding="utf-8", newline="") as stream:
                rows = csv.reader(stream, delimiter="\t" if suffix == ".tsv" else ",")
                next(rows, None)
                if not isinstance(locator, str) or not locator.startswith("row:"):
                    return False
                index = int(locator[4:])
                for row_index, row in enumerate(rows):
                    if row_index == index:
                        return patient in row
            return False
        content = path.read_text(encoding="utf-8")
        if suffix != ".json":
            return locator == "doc" and patient in content
        document = json.loads(content)
        if locator == "doc":
            return _contains_value(document, patient)
        if not isinstance(locator, str):
            return False
        return _contains_value(_get_pointer(document, _parse_pointer(locator)), patient)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, ProcessingError):
        return False


def _contains_value(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, Mapping):
        return any(_contains_value(child, expected) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_value(child, expected) for child in value)
    return False
