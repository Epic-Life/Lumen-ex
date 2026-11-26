"""Command-line entry point with count-only logging and safe diagnostics."""

import argparse
import logging
import os
from pathlib import Path

from lumen_ex.baseline import run_sanitizer
from lumen_ex.config import load_config
from lumen_ex.errors import LumenError


_LOGGER = logging.getLogger("lumen_ex")


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lumen-ex offline processing foundation")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, help="policy file (defaults to config/policy.yaml)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        config = load_config(args.config)
        input_root = args.input.resolve()
        output_root = args.output.resolve()
        if not input_root.is_dir() or _paths_overlap(input_root, output_root):
            raise LumenError("input_output_path_invalid")
        if not os.environ.get("RAKSHA_SECRET"):
            raise LumenError("secret_missing")
        result = run_sanitizer(input_root, output_root, config)
        return result
    except LumenError as error:
        _LOGGER.error("Processing stopped: %s", error.code)
        return 2
    except OSError:
        _LOGGER.error("Processing stopped: filesystem_error")
        return 2
