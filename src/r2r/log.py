"""Structured (JSON-lines) logging with a filter that redacts secrets."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TextIO

from pydantic import SecretStr

from r2r.settings import Secrets

MASK = "***"
_MIN_SECRET_LEN = 8
_PATTERNS = re.compile(
    r"sk-ant-[\w-]+|sk-or-[\w-]+|sk-lf-[\w-]+|pk-lf-[\w-]+|AIza[\w-]{20,}"
    r"|sk-[A-Za-z0-9]{20,}|(?<=Bearer )[\w.~+/=-]+"
)
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask known secret values (8+ chars) and anything shaped like an API key or bearer token."""
    for s in secrets:
        if len(s) >= _MIN_SECRET_LEN:
            text = text.replace(s, MASK)
    return _PATTERNS.sub(MASK, text)


def secret_values(secrets: Secrets) -> list[str]:
    return [v.get_secret_value() for v in secrets.__dict__.values() if isinstance(v, SecretStr)]


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = list(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self.secrets)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _STD_ATTRS and isinstance(value, str):
                setattr(record, key, redact(value, self.secrets))
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info),
                                     self.secrets)  # fmt: skip
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out.update({k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS})
        if record.exc_text:
            out["exc"] = record.exc_text
        return json.dumps(out, default=str)


def configure_logging(
    level: str = "INFO", secrets: Secrets | None = None, stream: TextIO | None = None
) -> None:
    """Send `r2r.*` logs as redacted JSON lines to `stream` (stderr by default)."""
    root = logging.getLogger("r2r")
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(secret_values(secrets) if secrets else ()))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("r2r") else f"r2r.{name}")
