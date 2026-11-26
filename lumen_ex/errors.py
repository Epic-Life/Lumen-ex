"""Safe, structured errors for application and pipeline boundaries."""

SAFE_ERROR_CODES = frozenset(
    {
        "configuration_unavailable",
        "configuration_encoding_invalid",
        "configuration_syntax_invalid",
        "configuration_key_invalid",
        "configuration_value_invalid",
        "configuration_value_out_of_range",
        "input_output_path_invalid",
        "secret_missing",
        "filesystem_error",
        "invalid_record",
        "leak_scan_failed",
        "unsupported_format",
        "empty_file",
        "undecodable_text",
        "RecursionError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "JSONDecodeError",
        "Error",
    }
)


class LumenError(Exception):
    """Base error whose code is safe to expose in logs and reports."""

    def __init__(self, code: str) -> None:
        if code not in SAFE_ERROR_CODES:
            raise ValueError("error_code_invalid")
        self.code = code
        super().__init__(code)


class ConfigurationError(LumenError):
    """Raised when an application configuration is invalid."""


class ProcessingError(LumenError):
    """Raised when a record cannot safely continue through the pipeline."""
