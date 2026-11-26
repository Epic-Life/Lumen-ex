"""Streaming readers for supported source formats and record locators."""

from contextlib import contextmanager
import codecs
import csv
import hashlib
import io
import os
from pathlib import Path
from typing import Iterator

import ijson
from ijson.common import JSONError

from lumen_ex.contracts.models import InputFileMetadata, ParsedRecord
from lumen_ex.errors import ProcessingError


_HASH_CHUNK_SIZE = 1024 * 1024
_JSON_RECORD_ARRAYS = {
    "patients",
    "visits",
    "records",
    "items",
    "results",
    "data",
    "resources",
}
_SUPPORTED_SUFFIXES = {".csv", ".tsv", ".json", ".txt"}


class _Utf8ByteReader:
    """Expose decoded text as a UTF-8 byte stream for the incremental JSON parser."""

    def __init__(self, text_stream: io.TextIOBase) -> None:
        self._text_stream = text_stream

    def read(self, size: int = -1) -> bytes:
        return self._text_stream.read(size).encode("utf-8")


class StreamingIngestor:
    """Discover inputs and stream parsed records without materializing whole datasets."""

    def __init__(self) -> None:
        self._input_root: Path | None = None

    def discover(self, input_root: Path) -> Iterator[InputFileMetadata]:
        root = input_root.resolve()
        if not root.is_dir():
            raise ProcessingError("input_output_path_invalid")
        self._input_root = root
        return self._discover_from(root)

    def read(self, source: InputFileMetadata) -> Iterator[ParsedRecord]:
        path = self._resolve_source(source)
        suffix = path.suffix.casefold()
        if suffix not in _SUPPORTED_SUFFIXES:
            raise ProcessingError("unsupported_format")
        if source.size_bytes == 0:
            raise ProcessingError("empty_file")
        if suffix in {".csv", ".tsv"}:
            return self._read_csv(source, path, "\t" if suffix == ".tsv" else ",")
        if suffix == ".json":
            return self._read_json(source, path)
        return self._read_text(source, path)

    def iter_batches(
        self, input_root: Path, batch_size: int = 1000
    ) -> Iterator[tuple[ParsedRecord, ...]]:
        """Yield globally ordered bounded batches across all discovered files."""
        if batch_size < 1:
            raise ValueError("batch_size_invalid")
        batch: list[ParsedRecord] = []
        for source in self.discover(input_root):
            for record in self.read(source):
                batch.append(record)
                if len(batch) == batch_size:
                    yield tuple(batch)
                    batch.clear()
        if batch:
            yield tuple(batch)

    def _discover_from(self, root: Path) -> Iterator[InputFileMetadata]:
        for directory, subdirectories, filenames in os.walk(
            root, followlinks=False, onerror=_raise_filesystem_error
        ):
            subdirectories[:] = sorted(
                name
                for name in subdirectories
                if not (Path(directory) / name).is_symlink()
            )
            for filename in sorted(filenames):
                path = Path(directory) / filename
                if path.is_symlink() or not path.is_file():
                    continue
                relative_path = path.relative_to(root).as_posix()
                stat = path.stat()
                yield InputFileMetadata(
                    relative_path=relative_path,
                    sha256=_sha256_file(path),
                    size_bytes=stat.st_size,
                    media_type=_media_type(path.suffix.casefold()),
                )

    def _resolve_source(self, source: InputFileMetadata) -> Path:
        if self._input_root is None:
            raise ProcessingError("invalid_record")
        path = (self._input_root / Path(source.relative_path)).resolve()
        try:
            path.relative_to(self._input_root)
        except ValueError:
            raise ProcessingError("invalid_record") from None
        if not path.is_file():
            raise ProcessingError("filesystem_error")
        return path

    def _read_csv(
        self, source: InputFileMetadata, path: Path, delimiter: str
    ) -> Iterator[ParsedRecord]:
        try:
            with _open_text(path) as text_stream:
                reader = csv.DictReader(text_stream, delimiter=delimiter)
                for index, row in enumerate(reader):
                    yield ParsedRecord(source, f"row:{index}", dict(row))
        except csv.Error:
            raise ProcessingError("CSVError") from None
        except UnicodeError:
            raise ProcessingError("UnicodeDecodeError") from None

    def _read_json(
        self, source: InputFileMetadata, path: Path
    ) -> Iterator[ParsedRecord]:
        try:
            root_type, arrays = _json_layout(path)
            if root_type == "Bundle":
                for index, entry in enumerate(
                    _json_items(path, "entry.item")
                ):
                    if not isinstance(entry, dict):
                        continue
                    resource = entry.get("resource")
                    if not isinstance(resource, dict) or resource.get("resourceType") != "Patient":
                        continue
                    yield ParsedRecord(
                        source,
                        f"/entry/{index}/resource",
                        resource,
                    )
                return

            if arrays:
                for array_prefix in arrays:
                    items_prefix = f"{array_prefix}.item" if array_prefix else "item"
                    for index, value in enumerate(
                        _json_items(path, items_prefix)
                    ):
                        locator = _array_item_pointer(array_prefix, index)
                        yield ParsedRecord(
                            source,
                            locator,
                            value if isinstance(value, dict) else {"value": value},
                        )
                return

            documents = _json_items(path, "")
            for document in documents:
                yield ParsedRecord(
                    source,
                    "doc",
                    document if isinstance(document, dict) else {"value": document},
                )
        except (JSONError, ValueError, UnicodeError):
            raise ProcessingError("JSONDecodeError") from None

    def _read_text(
        self, source: InputFileMetadata, path: Path
    ) -> Iterator[ParsedRecord]:
        try:
            with _open_text(path) as text_stream:
                yield ParsedRecord(source, "doc", {"text": text_stream.read()})
        except UnicodeError:
            raise ProcessingError("UnicodeDecodeError") from None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(suffix: str) -> str | None:
    return {
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".txt": "text/plain",
    }.get(suffix)


@contextmanager
def _open_text(path: Path) -> Iterator[io.TextIOWrapper]:
    with path.open("rb") as binary:
        encoding = _detect_encoding(binary)
        binary.seek(0)
        yield io.TextIOWrapper(binary, encoding=encoding, newline="")


def _detect_encoding(binary: io.BufferedReader) -> str:
    prefix = binary.read(4)
    binary.seek(0)
    if prefix.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"

    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        for chunk in iter(lambda: binary.read(_HASH_CHUNK_SIZE), b""):
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "cp1252"
    finally:
        binary.seek(0)
    return "utf-8"


def _json_layout(path: Path) -> tuple[str | None, tuple[str, ...]]:
    root_type: str | None = None
    arrays: list[str] = []
    with _open_text(path) as text_stream:
        for prefix, event, value in ijson.parse(_Utf8ByteReader(text_stream)):
            if prefix == "resourceType" and event == "string":
                root_type = value
            if event != "start_array":
                continue
            if not prefix:
                arrays.append("")
                continue
            segments = prefix.split(".")
            if "item" in segments:
                continue
            if segments[-1].casefold() in _JSON_RECORD_ARRAYS:
                arrays.append(prefix)
    if root_type == "Bundle":
        return root_type, ("entry",)
    return root_type, tuple(arrays)


def _json_items(path: Path, prefix: str) -> Iterator[object]:
    with _open_text(path) as text_stream:
        yield from ijson.items(_Utf8ByteReader(text_stream), prefix)


def _array_item_pointer(prefix: str, index: int) -> str:
    path = "" if not prefix else "/" + "/".join(
        segment.replace("~", "~0").replace("/", "~1")
        for segment in prefix.split(".")
    )
    return f"{path}/{index}"


def _raise_filesystem_error(_error: OSError) -> None:
    raise ProcessingError("filesystem_error") from None


__all__ = ["StreamingIngestor"]
