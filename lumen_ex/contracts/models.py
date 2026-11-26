"""Internal contracts kept separate from the evaluator's serialized formats."""

from dataclasses import dataclass, field
from enum import Enum
import re

from lumen_ex.errors import SAFE_ERROR_CODES


def _safe_relative_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    return all(part not in ("", ".", "..") for part in normalized.split("/"))


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProcessingStatus(str, Enum):
    SANITIZED = "sanitized"
    QUARANTINED = "quarantined"
    SUPPRESSED = "suppressed"


class LinkageAction(str, Enum):
    LINK = "LINK"
    DO_NOT_LINK = "DO_NOT_LINK"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class InputFileMetadata:
    relative_path: str = field(repr=False)
    sha256: str
    size_bytes: int
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not _safe_relative_path(self.relative_path):
            raise ValueError("input_path_invalid")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("input_sha256_invalid")
        if self.size_bytes < 0:
            raise ValueError("input_size_invalid")


@dataclass(frozen=True)
class ParsedRecord:
    source: InputFileMetadata
    locator: str
    values: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class NormalizedRecord:
    source: InputFileMetadata
    locator: str
    fields: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class DetectionResult:
    identifier_count: int
    entity_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.identifier_count < 0 or any(count < 0 for count in self.entity_counts.values()):
            raise ValueError("detection_count_invalid")


@dataclass(frozen=True)
class LinkageDecision:
    action: LinkageAction
    patient_reference: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.action is LinkageAction.LINK) != (self.patient_reference is not None):
            raise ValueError("linkage_reference_invalid")
        if self.action is LinkageAction.LINK and not self.patient_reference:
            raise ValueError("linkage_reference_invalid")


@dataclass(frozen=True)
class SanitizationResult:
    status: ProcessingStatus
    locator: str
    output_fields: dict[str, object] = field(default_factory=dict, repr=False)
    error_code: str | None = None
    identifiers_detected: int = 0
    generalized: bool = False
    suppressed: bool = False

    def __post_init__(self) -> None:
        if self.identifiers_detected < 0:
            raise ValueError("sanitization_count_invalid")
        if self.status is ProcessingStatus.QUARANTINED and not self.error_code:
            raise ValueError("quarantine_reason_required")
        if self.error_code is not None and self.error_code not in SAFE_ERROR_CODES:
            raise ValueError("error_code_invalid")
        if self.status is not ProcessingStatus.QUARANTINED and self.error_code is not None:
            raise ValueError("error_code_status_invalid")


@dataclass(frozen=True)
class OutputManifestEntry:
    input_sha256: str
    output: str | None
    status: ProcessingStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.input_sha256):
            raise ValueError("manifest_sha256_invalid")
        if self.status is ProcessingStatus.SANITIZED and not self.output:
            raise ValueError("manifest_output_required")
        if self.status is ProcessingStatus.SANITIZED and (
            not _safe_relative_path(self.output)
            or not self.output.startswith("sanitized/")
        ):
            raise ValueError("manifest_output_invalid")
        if self.status is ProcessingStatus.QUARANTINED and not self.reason:
            raise ValueError("manifest_reason_required")
        if self.status is ProcessingStatus.QUARANTINED and self.output is not None:
            raise ValueError("manifest_quarantine_output_invalid")
        if self.reason is not None and self.reason not in SAFE_ERROR_CODES:
            raise ValueError("manifest_reason_invalid")
        if self.status is not ProcessingStatus.QUARANTINED and self.reason is not None:
            raise ValueError("manifest_reason_status_invalid")

    def to_official_dict(self) -> dict[str, str | None]:
        """Serialize only fields permitted by docs/SPEC.md for a file entry."""
        entry: dict[str, str | None] = {
            "input_sha256": self.input_sha256,
            "output": self.output,
            "status": self.status.value,
        }
        if self.reason is not None:
            entry["reason"] = self.reason
        return entry


@dataclass(frozen=True)
class PipelineReport:
    files: tuple[OutputManifestEntry, ...]
    records_processed: int = 0
    identifiers_detected: int = 0
    records_linked: int = 0
    records_generalized: int = 0
    records_suppressed: int = 0
    records_quarantined: int = 0
    seconds: float = 0.0
    peak_rss_mb: float = 0.0
    entity_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        numeric_values = (
            self.records_processed,
            self.identifiers_detected,
            self.records_linked,
            self.records_generalized,
            self.records_suppressed,
            self.records_quarantined,
            self.seconds,
            self.peak_rss_mb,
        )
        if any(value < 0 for value in numeric_values) or any(
            value < 0 for value in self.entity_counts.values()
        ):
            raise ValueError("report_metric_invalid")

    def to_official_dict(self) -> dict[str, object]:
        """Serialize the existing raksha-report/1 contract without new required fields."""
        sanitized = sum(item.status is ProcessingStatus.SANITIZED for item in self.files)
        quarantined = sum(item.status is ProcessingStatus.QUARANTINED for item in self.files)
        report: dict[str, object] = {
            "schema": "raksha-report/1",
            "files": [item.to_official_dict() for item in self.files],
            "counts": {
                "files_total": len(self.files),
                "files_sanitized": sanitized,
                "files_quarantined": quarantined,
                "records_processed": self.records_processed,
                "identifiers_detected": self.identifiers_detected,
                "records_linked": self.records_linked,
                "records_generalized": self.records_generalized,
                "records_suppressed": self.records_suppressed,
                "records_quarantined": self.records_quarantined,
            },
            "timing": {"seconds": self.seconds},
            "resources": {"peak_rss_mb": self.peak_rss_mb},
        }
        if self.entity_counts:
            report["entities"] = dict(self.entity_counts)
        return report
