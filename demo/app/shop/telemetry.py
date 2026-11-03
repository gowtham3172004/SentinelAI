"""Structured JSON logging with W3C trace context.

One JSON object per line, with field names following OpenTelemetry semantic conventions
(``service.name``, ``exception.type``, ``http.route`` and so on), so any log pipeline, and
Sentinel's ingest, can consume it without a custom parser.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from shop.config import ShopSettings

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")
_trace: ContextVar[tuple[str, str] | None] = ContextVar("trace", default=None)


def start_trace(traceparent: str | None) -> tuple[str, str]:
    """Continue the caller's trace if a valid ``traceparent`` is given, else start one."""
    match = _TRACEPARENT.match(traceparent or "")
    trace_id = match.group(1) if match else secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    _trace.set((trace_id, span_id))
    return trace_id, span_id


def current_traceparent() -> str | None:
    context = _trace.get()
    return None if context is None else f"00-{context[0]}-{context[1]}-01"


def exception_type_name(exc_type: type[BaseException]) -> str:
    module = exc_type.__module__
    return exc_type.__qualname__ if module == "builtins" else f"{module}.{exc_type.__qualname__}"


class JsonFormatter(logging.Formatter):
    def __init__(self, settings: ShopSettings) -> None:
        super().__init__()
        self._resource = {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
            "vcs.revision": settings.git_sha,
            "deployment.environment": settings.environment,
        }

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            **self._resource,
        }
        context = _trace.get()
        if context is not None:
            entry["trace_id"], entry["span_id"] = context
        attributes = getattr(record, "attributes", None)
        if isinstance(attributes, dict):
            entry.update(attributes)
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc, tb = record.exc_info
            entry["exception.type"] = exception_type_name(exc_type)
            entry["exception.message"] = str(exc)
            entry["exception.stacktrace"] = "".join(traceback.format_exception(exc_type, exc, tb))
        return json.dumps(entry, default=str)


def configure_logging(settings: ShopSettings) -> None:
    formatter = JsonFormatter(settings)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_file:
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Our middleware writes the access log; silence the server's duplicate.
    logging.getLogger("uvicorn.access").disabled = True
