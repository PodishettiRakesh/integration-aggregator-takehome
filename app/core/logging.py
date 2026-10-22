from __future__ import annotations

import logging
import re
from typing import Any

_REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = {
    "access_token",
    "client_secret",
    "code",
    "id_token",
    "openbao_token",
    "password",
    "refresh_token",
    "secret",
    "token",
    "x-vault-token",
}

_PATTERN = re.compile(
    r"(?i)(access_token|client_secret|id_token|refresh_token|openbao_token|"
    r"x-vault-token|authorization|bearer|code|password|secret|token)"
    r"([\"'\s:=]+)([^\s&\"']+)"
)


def redact_text(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        secret = match.group(3)
        if secret.startswith("%"):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{_REDACTED}"

    return _PATTERN.sub(_replace, value)


def redact_value(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_value(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_value(record.args)
            else:
                record.args = tuple(redact_value(arg) for arg in record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root.addHandler(handler)
    for handler in root.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(RedactingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
