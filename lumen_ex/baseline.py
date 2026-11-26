"""Run the file pipeline with Lumen-ex detection and validated policy."""

from pathlib import Path

from lumen_ex.config import PipelineConfig
from lumen_ex.label_redaction import is_private_column, redact_labeled_names


def run_sanitizer(input_root: Path, output_root: Path, config: PipelineConfig) -> int:
    from starter import main as starter

    previous_policy = starter.POLICY
    try:
        starter.POLICY = {
            "age_band_years": config.age_band_years,
            "date_shift_max_days": config.date_shift_max_days,
            "min_group_size": config.min_group_size,
        }
        return starter.run(
            input_root,
            output_root,
            text_detector=redact_labeled_names,
            private_column=is_private_column,
        )
    finally:
        starter.POLICY = previous_policy
