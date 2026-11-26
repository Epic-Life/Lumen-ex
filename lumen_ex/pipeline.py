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
        except ProcessingError as error:
            result = self._quarantine(record, error.code)
        except Exception:
            result = self._quarantine(record, "internal_error")

        try:
            is_safe = self._stages.leak_scanner.is_safe(result)
        except Exception:
            return self._quarantine(record, "internal_error")
        if not is_safe:
            return self._quarantine(record, "leak_scan_failed")
        return result

    def run(self, records: Iterable[ParsedRecord]) -> None:
        """Normalize and process records in order, then finalize reporting."""
        manifest: list[OutputManifestEntry] = []
        for record in records:
            result = self.process(record)
            if result.status is ProcessingStatus.SANITIZED:
                try:
                    self._stages.output_writer.write(record.source, result)
                except Exception:
                    raise ProcessingError("internal_error") from None
            try:
                entry = self._stages.reporter.record(record.source, result)
            except Exception:
                raise ProcessingError("internal_error") from None
            if not self._is_safe_metadata(entry.to_official_dict()):
                raise ProcessingError("leak_scan_failed")
            manifest.append(entry)

        report = PipelineReport(files=tuple(manifest))
        if not self._is_safe_metadata(report.to_official_dict()):
            raise ProcessingError("leak_scan_failed")
        try:
            self._stages.reporter.finalize(report)
        except Exception:
            raise ProcessingError("internal_error") from None

    def _is_safe_metadata(self, value: object) -> bool:
        candidate = SanitizationResult(
            status=ProcessingStatus.SANITIZED,
            locator="metadata",
            output_fields={"metadata": value},
        )
        try:
            return self._stages.leak_scanner.is_safe(candidate)
        except Exception:
            return False

    @staticmethod
    def _quarantine(record: ParsedRecord, error_code: str) -> SanitizationResult:
        return SanitizationResult(
            status=ProcessingStatus.QUARANTINED,
            locator=record.locator,
            error_code=error_code,
        )

    def ingest_directory(
        self, input_root: Path, ingestor: Ingestor | None = None
    ) -> IngestionResult:
        """Expose file-level ingestion outcomes before records enter later stages."""
        return (ingestor or StreamingIngestor()).ingest(input_root)


def abstain_from_linking() -> LinkageDecision:
    """Default contract decision for records without a future linkage implementation."""
    return LinkageDecision(action=LinkageAction.ABSTAIN)
