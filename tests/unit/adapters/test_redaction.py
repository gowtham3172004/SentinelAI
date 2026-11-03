from __future__ import annotations

import logging
import secrets

import pytest

from sentinelai.adapters.redaction import MIN_SECRET_LENGTH, redact, register_secret


def test_registered_secret_is_redacted_from_any_logger(caplog: pytest.LogCaptureFixture) -> None:
    secret = "tok-" + secrets.token_hex(16)
    register_secret(secret)
    caplog.set_level(logging.DEBUG)
    logging.getLogger("some.library.deep.child").debug("headers=%s", {"auth": f"Bearer {secret}"})
    logging.getLogger("httpcore.http11").debug(f"inline {secret} in message")
    assert secret not in caplog.text
    assert caplog.text.count("***") >= 2


def test_short_values_are_not_registered() -> None:
    short = "abc"
    assert len(short) < MIN_SECRET_LENGTH
    register_secret(short)
    register_secret(None)
    register_secret("")
    assert redact("abc abc") == "abc abc"


def test_redact_handles_overlapping_secrets() -> None:
    base = "overlap-" + secrets.token_hex(8)
    register_secret(base)
    register_secret(base + "-extended")
    assert redact(f"x {base}-extended y {base}") == "x *** y ***"


def test_unrelated_records_are_untouched(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    logging.getLogger("plain").info("value=%d", 42)
    assert "value=42" in caplog.text
