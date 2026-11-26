"""Stage interfaces; implementations are intentionally deferred to later milestones."""

from pathlib import Path
from typing import Iterable, Iterator, Protocol

from lumen_ex.contracts.models import (
    DetectionResult,
    InputFileMetadata,
    LinkageDecision,
    NormalizedRecord,
    OutputManifestEntry,
    ParsedRecord,
    PipelineReport,
    SanitizationResult,
)


class Ingestor(Protocol):
    def discover(self, input_root: Path) -> Iterable[InputFileMetadata]: ...

    def read(self, source: InputFileMetadata) -> Iterator[ParsedRecord]: ...


class Normalizer(Protocol):
    def normalize(self, record: ParsedRecord) -> NormalizedRecord: ...


class Detector(Protocol):
    def detect(self, record: NormalizedRecord) -> DetectionResult: ...


class Linker(Protocol):
    def decide(self, record: NormalizedRecord) -> LinkageDecision: ...


class PrivacyTransformer(Protocol):
    def transform(
        self,
        record: NormalizedRecord,
        detection: DetectionResult,
        linkage: LinkageDecision,
    ) -> SanitizationResult: ...


class LeakScanner(Protocol):
    def is_safe(self, result: SanitizationResult) -> bool: ...


class OutputWriter(Protocol):
    def write(self, source: InputFileMetadata, result: SanitizationResult) -> None: ...


class Reporter(Protocol):
    def record(
        self, source: InputFileMetadata, result: SanitizationResult
    ) -> OutputManifestEntry: ...

    def finalize(self, report: PipelineReport) -> None: ...
