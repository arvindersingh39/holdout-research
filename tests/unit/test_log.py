"""Structured logging never emits secrets."""

import io
import json
import logging

from pydantic import SecretStr

from r2r.log import RedactingFilter, configure_logging, get_logger, redact
from r2r.settings import Secrets


def test_redact_known_values_and_patterns() -> None:
    secret = "my-very-private-token-123"
    text = (
        f"value={secret} anthropic=sk-ant-api03-AbC_dEf-123456 router=sk-or-v1-abcdef0123456789 "
        "lf=sk-lf-1234abcd pk=pk-lf-5678efgh google=AIzaSyA1234567890abcdefghijk "
        "hdr=Bearer eyJhbGciOi.payload.sig"
    )
    out = redact(text, [secret])
    for leaked in [
        secret,
        "sk-ant-api03",
        "sk-or-v1",
        "sk-lf-1234",
        "pk-lf-5678",
        "AIzaSy",
        "eyJhb",
    ]:
        assert leaked not in out, leaked
    assert out.count("***") >= 7


def test_short_values_not_redacted_everywhere() -> None:
    # A 3-char "secret" would mangle normal text; only values of 8+ chars are masked.
    assert redact("the cat sat", ["cat"]) == "the cat sat"


def test_logger_output_is_json_and_redacted() -> None:
    secrets = Secrets(anthropic_api_key=SecretStr("sk-ant-REALKEY-0123456789"))
    stream = io.StringIO()
    configure_logging(level="INFO", secrets=secrets, stream=stream)
    log = get_logger("r2r.test")
    log.info("calling with key %s", "sk-ant-REALKEY-0123456789", extra={"role": "coder",
             "header": "x-api-key: sk-ant-REALKEY-0123456789"})  # fmt: skip
    line = stream.getvalue().strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["level"] == "INFO"
    assert rec["logger"] == "r2r.test"
    assert rec["role"] == "coder"
    assert "REALKEY" not in line


def test_filter_handles_exceptions() -> None:
    f = RedactingFilter(["supersecretvalue1"])
    record = logging.LogRecord("r2r", logging.ERROR, __file__, 1, "boom %s", ("supersecretvalue1",),
                               None)  # fmt: skip
    assert f.filter(record)
    assert "supersecretvalue1" not in record.getMessage()
