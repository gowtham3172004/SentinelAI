"""Normalised telemetry, stack-trace parsing and exception fingerprinting (D2 section 5).

Designed for arbitrary projects, not one demo:
- ``FieldMapping`` reads common JSON log dialects (OpenTelemetry semantic conventions,
  ECS-style ``@timestamp``/``error.*``, logfmt-derived keys, bunyan/pino ``msg``/numeric
  ``level``) and can be overridden per source.
- Stack parsers cover Python, JVM (Java/Kotlin/Scala) and JavaScript/Node. Without a stack
  trace, a fingerprint still forms from the exception type and the masked message.
- Fingerprints are stable under changed line numbers, IDs and values (D2 section 5.4).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sentinelai.domain.redaction import redact_text, redact_value

MAX_MESSAGE_CHARS = 4000
MAX_STACK_CHARS = 32_000
MAX_ATTRIBUTES_CHARS = 8000
LEVELS = ("debug", "info", "warn", "error", "fatal")


# --------------------------------------------------------------------------- field mapping


@dataclass(frozen=True)
class FieldMapping:
    """Candidate keys per normalised field, first match wins. Dotted keys also match nested
    objects (``exception.type`` matches ``{"exception": {"type": ...}}``)."""

    timestamp: tuple[str, ...] = ("ts", "timestamp", "@timestamp", "time", "datetime", "date")
    level: tuple[str, ...] = ("level", "severity", "severity_text", "levelname", "log.level", "lvl")
    message: tuple[str, ...] = ("message", "msg", "body", "event", "log")
    logger: tuple[str, ...] = ("logger", "logger_name", "log.logger", "name", "module")
    service: tuple[str, ...] = ("service.name", "service", "app", "application", "component")
    version: tuple[str, ...] = ("service.version", "version", "app_version", "release")
    revision: tuple[str, ...] = ("vcs.revision", "git_sha", "git.commit", "commit", "revision")
    environment: tuple[str, ...] = ("deployment.environment", "environment", "env")
    trace_id: tuple[str, ...] = ("trace_id", "traceId", "trace.id", "dd.trace_id")
    span_id: tuple[str, ...] = ("span_id", "spanId", "span.id", "dd.span_id")
    http_method: tuple[str, ...] = ("http.method", "http.request.method", "method")
    http_route: tuple[str, ...] = (
        "http.route",
        "route",
        "endpoint",
        "url.path",
        "http.target",
        "path",
    )
    http_status: tuple[str, ...] = (
        "http.status_code",
        "http.response.status_code",
        "status_code",
        "status",
        "response.status",
    )
    duration_ms: tuple[str, ...] = ("duration_ms", "latency_ms", "elapsed_ms", "response_time_ms")
    duration_s: tuple[str, ...] = ("duration", "duration_s", "latency", "elapsed", "response_time")
    exception_type: tuple[str, ...] = (
        "exception.type",
        "error.type",
        "exc_type",
        "error.kind",
        "err.type",
        "error_class",
    )
    exception_message: tuple[str, ...] = (
        "exception.message",
        "error.message",
        "exc_message",
        "err.message",
        "error",
    )
    stacktrace: tuple[str, ...] = (
        "exception.stacktrace", "error.stack_trace", "stack_trace", "stacktrace", "stack",
        "err.stack", "exc_info", "traceback",
    )  # fmt: skip


def lookup(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
        node: Any = record
        for part in key.split("."):
            if not isinstance(node, Mapping) or part not in node:
                node = None
                break
            node = node[part]
        if node not in (None, "") and not isinstance(node, Mapping):
            return node
    return None


_NUMERIC_LEVELS = {10: "debug", 20: "info", 30: "info", 40: "warn", 50: "error", 60: "fatal"}
_LEVEL_ALIASES = {
    "trace": "debug", "debug": "debug", "info": "info", "information": "info", "notice": "info",
    "warn": "warn", "warning": "warn", "error": "error", "err": "error", "critical": "fatal",
    "fatal": "fatal", "crit": "fatal", "alert": "fatal", "emergency": "fatal", "panic": "fatal",
}  # fmt: skip


def normalise_level(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Python logging uses multiples of 10; bunyan/pino 10..60.
        return _NUMERIC_LEVELS.get(int(value) // 10 * 10, "error" if value >= 50 else "info")
    return _LEVEL_ALIASES.get(str(value).strip().lower(), "info")


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 1e17:  # nanoseconds since the epoch
            number /= 1e9
        elif number > 1e14:  # microseconds
            number /= 1e6
        elif number > 1e11:  # milliseconds
            number /= 1e3
        return datetime.fromtimestamp(number, UTC)
    if isinstance(value, str) and value:
        text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


# --------------------------------------------------------------------------- stack traces


@dataclass(frozen=True)
class Frame:
    path: str
    function: str
    line: int | None
    in_app: bool = False


_PYTHON_FRAME = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<fn>[^\s]+)')
_JVM_FRAME = re.compile(r"^\s*at (?P<fn>[\w$.<>]+)\((?P<file>[^:)]+)(?::(?P<line>\d+))?\)", re.M)
_JS_FRAME = re.compile(
    r"^\s*at (?:(?P<fn>[^\s(]+) \()?(?P<path>[^():]+(?::[^():]+)?):(?P<line>\d+):\d+\)?", re.M
)
_LIBRARY_MARKERS = (
    "site-packages/", "dist-packages/", "/lib/python", "node_modules/", "/.venv/", "/venv/",
    "<frozen ", "node:internal", "node:",
)  # fmt: skip
_JVM_LIBRARY_PACKAGES = (
    "java.", "javax.", "jdk.", "sun.", "kotlin.", "scala.", "org.springframework.", "io.netty.",
    "org.apache.", "com.zaxxer.", "org.hibernate.", "com.fasterxml.",
)  # fmt: skip


def _relative(path: str, app_roots: Sequence[str]) -> str:
    clean = path.replace("\\", "/")
    for root in app_roots:
        root_clean = root.replace("\\", "/").rstrip("/") + "/"
        if root_clean != "/" and root_clean in clean:
            return clean[clean.index(root_clean) + len(root_clean) :]
    return clean


def classify_in_app(path: str, source_roots: Sequence[str]) -> bool:
    if any(marker in path for marker in _LIBRARY_MARKERS):
        return False
    if not source_roots:
        return True  # no configuration: anything that is not clearly a library is the app's
    return any(path.startswith(root.rstrip("/") + "/") or path == root for root in source_roots)


def parse_stack(
    text: str, *, source_roots: Sequence[str] = (), app_roots: Sequence[str] = ()
) -> list[Frame]:
    """Frames ordered outermost first, innermost (where it was raised) last."""
    frames: list[Frame] = []
    python = list(_PYTHON_FRAME.finditer(text))
    if python:
        for match in python:
            path = _relative(match["path"], app_roots)
            frames.append(Frame(path, match["fn"], int(match["line"]),
                                classify_in_app(path, source_roots)))  # fmt: skip
        return frames
    jvm = list(_JVM_FRAME.finditer(text))
    if jvm:  # JVM lists innermost first: reverse to outermost-first
        for match in reversed(jvm):
            qualified = match["fn"]
            package_path = qualified.rsplit(".", 2)[0].replace(".", "/")  # drop class.method
            path = f"{package_path}/{match['file']}"
            line = int(match["line"]) if match["line"] else None
            library = qualified.startswith(_JVM_LIBRARY_PACKAGES)
            frames.append(Frame(path, qualified.rsplit(".", 1)[-1], line,
                                not library and classify_in_app(path, source_roots)))  # fmt: skip
        return frames
    js = list(_JS_FRAME.finditer(text))
    for match in reversed(js):  # V8 also lists innermost first
        path = _relative(match["path"], app_roots)
        frames.append(Frame(path, match["fn"] or "<anonymous>", int(match["line"]),
                            classify_in_app(path, source_roots)))  # fmt: skip
    return frames


# --------------------------------------------------------------------------- fingerprint

_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\[REDACTED:[a-z_]+\]"), "<redacted>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?"), "<n>"),  # not digits glued to a word
)  # fmt: skip


def mask_message(message: str) -> str:
    for pattern, token in _MASKS:
        message = pattern.sub(token, message)
    return " ".join(message.split())[:500]


@dataclass(frozen=True)
class Fingerprint:
    value: str
    library_only: bool
    top_frames: tuple[str, ...]


def fingerprint(exception_type: str, message: str, frames: Sequence[Frame]) -> Fingerprint:
    """sha256(type | top-3 in-app frames (innermost first) | masked message)[:16]."""
    in_app = [f for f in reversed(frames) if f.in_app]
    chosen = in_app or list(reversed(frames))
    top = tuple(f"{f.path}:{f.function}" for f in chosen[:3])
    material = "|".join((exception_type, ",".join(top), mask_message(message)))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return Fingerprint(digest, library_only=not in_app and bool(frames), top_frames=top)


# --------------------------------------------------------------------------- normalised record


@dataclass(frozen=True)
class SourceDefaults:
    service: str | None = None
    environment: str | None = None
    source_roots: tuple[str, ...] = ()
    app_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogRecord:
    ts: datetime
    service: str
    level: str
    message: str
    logger: str | None = None
    version: str | None = None
    revision: str | None = None
    environment: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    http_method: str | None = None
    http_route: str | None = None
    http_status: int | None = None
    duration_ms: float | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    frames: tuple[Frame, ...] = ()
    fingerprint: Fingerprint | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_request(self) -> bool:
        return self.http_status is not None and self.http_route is not None

    @property
    def innermost_in_app(self) -> bool | None:
        return self.frames[-1].in_app if self.frames else None


class RejectedRecord(ValueError):
    """A record that cannot be normalised (kept in the dead-letter table with the reason)."""


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise(
    raw: Mapping[str, Any], *, mapping: FieldMapping, defaults: SourceDefaults, received: datetime
) -> LogRecord:
    ts = parse_timestamp(lookup(raw, mapping.timestamp)) or received
    service = lookup(raw, mapping.service) or defaults.service
    if not service:
        raise RejectedRecord("no service name in the record and no default for the source")
    message = redact_text(str(lookup(raw, mapping.message) or ""))[:MAX_MESSAGE_CHARS]
    exc_type = lookup(raw, mapping.exception_type)
    stack = lookup(raw, mapping.stacktrace)
    stack_text = redact_text(str(stack))[:MAX_STACK_CHARS] if stack else ""
    frames = tuple(parse_stack(stack_text, source_roots=defaults.source_roots,
                               app_roots=defaults.app_roots))  # fmt: skip
    if exc_type is None and frames:  # e.g. only a stack: take the type from its last line
        last = stack_text.strip().splitlines()[-1] if stack_text.strip() else ""
        exc_type = last.split(":", 1)[0].strip() or None
    exc_message = lookup(raw, mapping.exception_message)
    exc_message_text = redact_text(str(exc_message))[:MAX_MESSAGE_CHARS] if exc_message else None
    fp = None
    if exc_type:
        fp = fingerprint(str(exc_type), exc_message_text or message, frames)
    duration = _as_float(lookup(raw, mapping.duration_ms))
    if duration is None and (seconds := _as_float(lookup(raw, mapping.duration_s))) is not None:
        duration = seconds * 1000
    known = {k.split(".")[0] for keys in vars(mapping).values() for k in keys}
    extras = {k: v for k, v in raw.items() if k not in known and k.split(".")[0] not in known}
    attributes = redact_value(extras)
    if len(str(attributes)) > MAX_ATTRIBUTES_CHARS:
        attributes = {"_truncated": True}
    return LogRecord(
        ts=ts,
        service=str(service),
        level=normalise_level(lookup(raw, mapping.level) or ("error" if exc_type else "info")),
        message=message,
        logger=_str_or_none(lookup(raw, mapping.logger)),
        version=_str_or_none(lookup(raw, mapping.version)),
        revision=_str_or_none(lookup(raw, mapping.revision)),
        environment=_str_or_none(lookup(raw, mapping.environment)) or defaults.environment,
        trace_id=_str_or_none(lookup(raw, mapping.trace_id)),
        span_id=_str_or_none(lookup(raw, mapping.span_id)),
        http_method=_str_or_none(lookup(raw, mapping.http_method)),
        http_route=_str_or_none(lookup(raw, mapping.http_route)),
        http_status=_as_int(lookup(raw, mapping.http_status)),
        duration_ms=duration,
        exception_type=_str_or_none(exc_type),
        exception_message=exc_message_text,
        frames=frames,
        fingerprint=fp,
        attributes=attributes if isinstance(attributes, dict) else {},
    )


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)[:512]
