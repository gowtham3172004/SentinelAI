"""Process-wide secret redaction for log records and untrusted text.

Adapters register every credential they hold. From then on, each log record created anywhere
in the process (our loggers, httpx, httpcore, any library) has those values replaced with
``***`` *at creation time*. That covers every handler, including ones configured later by a
service or a test harness. Secrets shorter than ``MIN_SECRET_LENGTH`` are ignored so that
common short strings are never mangled.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

MIN_SECRET_LENGTH = 8
REDACTED = "***"


@dataclass
class _State:
    lock: threading.Lock = field(default_factory=threading.Lock)
    secrets: frozenset[str] = frozenset()
    installed: bool = False
    base_factory: Any = field(default_factory=logging.getLogRecordFactory)


_state = _State()


def redact(text: str) -> str:
    for secret in sorted(_state.secrets, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def _redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record: logging.LogRecord = _state.base_factory(*args, **kwargs)
    if not _state.secrets:
        return record
    try:
        message = record.getMessage()
    except Exception:  # a malformed record is the logging system's problem, not ours
        return record
    cleaned = redact(message)
    if cleaned != message:
        record.msg = cleaned
        record.args = None
    return record


def register_secret(value: str | None) -> None:
    """Redact ``value`` from all future log records in this process."""
    if not value or len(value) < MIN_SECRET_LENGTH:
        return
    with _state.lock:
        _state.secrets = _state.secrets | {value}
        if not _state.installed:
            logging.setLogRecordFactory(_redacting_factory)
            _state.installed = True
