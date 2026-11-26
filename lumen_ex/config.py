"""Validated loading for the starter's small YAML-like policy file."""

from dataclasses import dataclass
from pathlib import Path

from lumen_ex.errors import ConfigurationError


@dataclass(frozen=True)
class PipelineConfig:
    age_band_years: int = 5
    date_shift_max_days: int = 180
    min_group_size: int = 5


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "policy.yaml"
_EXPECTED_KEYS = {
    "age_band_years",
    "date_shift_max_days",
    "min_group_size",
}


def load_config(path: Path | str | None = None) -> PipelineConfig:
    """Load integer policy settings without requiring a YAML dependency."""
    config_path = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    values: dict[str, int] = {}
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise ConfigurationError("configuration_unavailable") from None
    except UnicodeError:
        raise ConfigurationError("configuration_encoding_invalid") from None

    for line in lines:
        content = line.split("#", 1)[0].strip()
        if not content:
            continue
        if ":" not in content:
            raise ConfigurationError("configuration_syntax_invalid")
        key, value = (part.strip() for part in content.split(":", 1))
        if key not in _EXPECTED_KEYS or key in values:
            raise ConfigurationError("configuration_key_invalid")
        try:
            values[key] = int(value)
        except ValueError:
            raise ConfigurationError("configuration_value_invalid") from None

    defaults = PipelineConfig()
    resolved = {
        "age_band_years": values.get("age_band_years", defaults.age_band_years),
        "date_shift_max_days": values.get("date_shift_max_days", defaults.date_shift_max_days),
        "min_group_size": values.get("min_group_size", defaults.min_group_size),
    }
    if (
        resolved["age_band_years"] < 1
        or resolved["date_shift_max_days"] < 1
        or resolved["min_group_size"] < 2
    ):
        raise ConfigurationError("configuration_value_out_of_range")
    return PipelineConfig(**resolved)
