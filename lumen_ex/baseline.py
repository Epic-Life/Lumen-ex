"""Compatibility adapter to run the official starter without extending its algorithms."""

import sys
from pathlib import Path

from lumen_ex.config import PipelineConfig
from lumen_ex.security import SensitiveDataLeakScanner


def run_starter(
    input_root: Path,
    output_root: Path,
    config: PipelineConfig,
    secret: str,
    leak_scanner: SensitiveDataLeakScanner,
) -> int:
    """Invoke the starter's existing implementation with validated policy values."""
    from starter import main as starter

    previous_argv = sys.argv
    previous_policy = starter.POLICY
    previous_scanner = starter.OUTPUT_LEAK_SCANNER
    try:
        sys.argv = [
            "starter/main.py",
            "--input",
            str(input_root),
            "--output",
            str(output_root),
        ]
        starter.POLICY = {
            "age_band_years": config.age_band_years,
            "date_shift_max_days": config.date_shift_max_days,
            "min_group_size": config.min_group_size,
        }
        starter.SECRET = secret.encode("utf-8")
        starter.OUTPUT_LEAK_SCANNER = leak_scanner
        return starter.main()
    finally:
        sys.argv = previous_argv
        starter.POLICY = previous_policy
        starter.OUTPUT_LEAK_SCANNER = previous_scanner
        starter.SECRET = b""
        leak_scanner.clear_canaries()
