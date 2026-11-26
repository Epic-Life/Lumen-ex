"""Small fail-closed coordinator over injected, replaceable processing stages."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from lumen_ex.contracts.models import (
    DetectionResult,
    IngestionResult,
    LinkageAction,
    LinkageDecision,
    OutputManifestEntry,
    ParsedRecord,
    PipelineReport,
    ProcessingStatus,
    SanitizationResult,
)
from lumen_ex.errors import ProcessingError
from lumen_ex.ingestion import StreamingIngestor
from lumen_ex.stages import (
    Detector,
    Ingestor,
    LeakScanner,
    Linker,
    Normalizer,
    OutputWriter,
    PrivacyTransformer,
    Reporter,
)


@dataclass(frozen=True)
class PipelineStages:
    normalizer: Normalizer
    detector: Detector
    linker: Linker
    privacy: PrivacyTransformer
    leak_scanner: LeakScanner
    output_writer: OutputWriter
    reporter: Reporter


class Pipeline:
    def __init__(self, stages: PipelineStages) -> None:
        self._stages = stages

    def process(self, record: ParsedRecord) -> SanitizationResult:
        """Process one record; stage failures become a safe quarantine result."""
        try:
            normalized = self._stages.normalizer.normalize(record)
            detection: DetectionResult = self._stages.detector.detect(normalized)
            linkage = self._stages.linker.decide(normalized)
            result = self._stages.privacy.transform(normalized, detection, linkage)
            if not self._stages.leak_scanner.is_safe(result):
                raise ProcessingError("leak_scan_failed")
            return result
        except ProcessingError as error:
            return SanitizationResult(
                status=ProcessingStatus.QUARANTINED,
                locator=record.locator,
                error_code=error.code,
            )

    def run(self, records: Iterable[ParsedRecord]) -> None:
        """Normalize and process records in order, then finalize reporting."""
        manifest: list[OutputManifestEntry] = []
        for record in records:
            result = self.process(record)
            if result.status is ProcessingStatus.SANITIZED:
                self._stages.output_writer.write(record.source, result)
            manifest.append(self._stages.reporter.record(record.source, result))
        self._stages.reporter.finalize(PipelineReport(files=tuple(manifest)))

    def ingest_directory(
        self, input_root: Path, ingestor: Ingestor | None = None
    ) -> IngestionResult:
        """Expose file-level ingestion outcomes before records enter later stages."""
        return (ingestor or StreamingIngestor()).ingest(input_root)


def abstain_from_linking() -> LinkageDecision:
    """Default contract decision for records without a future linkage implementation."""
    return LinkageDecision(action=LinkageAction.ABSTAIN)
