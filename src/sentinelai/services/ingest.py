"""Ingestion: parse, normalise, redact, sample and store telemetry idempotently (FR-01, FR-02).

Input formats, chosen to fit almost any production stack:
- JSON lines (any dialect, via FieldMapping): tailed files, stdin, or NDJSON over HTTP from
  Vector, Fluent Bit, Logstash and similar.
- OTLP/HTTP JSON log payloads from any OpenTelemetry Collector or SDK.
- Release and configuration events (D2 section 5.7) from CI/CD.

Raw records are kept only when they matter for investigation: warnings and errors,
exceptions, 5xx responses, and a deterministic sample of the rest. Aggregates always count
every record. That keeps Sentinel's storage proportional to *problems*, not traffic.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sentinelai.domain.redaction import redact_text
from sentinelai.domain.telemetry import (
    FieldMapping,
    LogRecord,
    RejectedRecord,
    SourceDefaults,
    normalise,
)
from sentinelai.services.telemetry_ports import ChangeIn, TelemetryStore

MAX_LINE_BYTES = 256 * 1024
MAX_DEAD_LETTER_CHARS = 4000


@dataclass(frozen=True)
class IngestPolicy:
    info_sample_rate: float = 0.01  # share of healthy info/debug records stored raw

    def keep_raw(self, record: LogRecord) -> bool:
        if record.level in ("warn", "error", "fatal") or record.exception_type:
            return True
        if record.http_status is not None and record.http_status >= 500:
            return True
        key = f"{record.trace_id or ''}|{record.ts.isoformat()}|{record.message[:64]}"
        bucket = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return bucket < self.info_sample_rate  # deterministic: a replay keeps the same rows


@dataclass(frozen=True)
class IngestResult:
    accepted: int
    rejected: int
    duplicate: bool = False


def parse_json_lines(lines: Iterable[str]) -> Iterator[tuple[dict[str, Any] | None, str, str]]:
    """Yield (object or None, reason, raw line) for each non-empty line."""
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if len(text.encode("utf-8", "replace")) > MAX_LINE_BYTES:
            yield None, "line_too_large", text[:200]
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            yield None, "not_json", text
            continue
        if not isinstance(value, dict):
            yield None, "not_an_object", text
            continue
        yield value, "", text


def _any_value(value: Mapping[str, Any]) -> Any:
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return int(value["intValue"])
    if "arrayValue" in value:
        return [_any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return {
            kv["key"]: _any_value(kv.get("value", {}))
            for kv in value["kvlistValue"].get("values", [])
        }
    return None


def flatten_otlp_logs(payload: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """OTLP/HTTP JSON ``ExportLogsServiceRequest`` into flat records with OTel key names."""
    for resource_logs in payload.get("resourceLogs", []):
        resource = {
            a["key"]: _any_value(a.get("value", {}))
            for a in resource_logs.get("resource", {}).get("attributes", [])
        }
        for scope_logs in resource_logs.get("scopeLogs", []):
            scope = scope_logs.get("scope", {}).get("name")
            for log in scope_logs.get("logRecords", []):
                record: dict[str, Any] = dict(resource)
                record.update({a["key"]: _any_value(a.get("value", {}))
                               for a in log.get("attributes", [])})  # fmt: skip
                body = _any_value(log.get("body", {}))
                if isinstance(body, dict):
                    record.update(body)
                elif body is not None:
                    record["message"] = body
                if log.get("timeUnixNano") or log.get("observedTimeUnixNano"):
                    record["timestamp"] = int(
                        log.get("timeUnixNano") or log["observedTimeUnixNano"]
                    )
                if log.get("severityText"):
                    record["level"] = log["severityText"]
                elif log.get("severityNumber"):  # OTel: 9-12 info, 13-16 warn, 17+ error
                    number = int(log["severityNumber"])
                    record["level"] = (
                        "error" if number >= 17 else "warn" if number >= 13 else "info"
                    )
                if log.get("traceId"):
                    record["trace_id"] = log["traceId"]
                if log.get("spanId"):
                    record["span_id"] = log["spanId"]
                if scope:
                    record.setdefault("logger", scope)
                yield record


class ChangeEventModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["deploy", "config_change", "rollback", "scale"]
    service: str = Field(min_length=1, max_length=200)
    environment: str = "production"
    version: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    previous_version: str | None = None
    git_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    changed_keys: list[str] = Field(default_factory=list, max_length=200)
    actor: str | None = Field(default=None, max_length=200)


def parse_change(raw: Mapping[str, Any]) -> ChangeIn:
    """Validate a release/config event; config *values* are never accepted, only key names."""
    try:
        model = ChangeEventModel.model_validate(raw)
    except ValidationError as exc:
        raise RejectedRecord(f"invalid change event: {exc.errors(include_input=False)}") from exc
    occurred = (
        model.occurred_at if model.occurred_at.tzinfo else model.occurred_at.replace(tzinfo=UTC)
    )
    return ChangeIn(
        kind=model.kind,
        service=model.service,
        environment=model.environment,
        version=model.version,
        occurred_at=occurred,
        idempotency_key=model.idempotency_key,
        previous_version=model.previous_version,
        git_sha=model.git_sha,
        changed_keys=tuple(model.changed_keys),
        actor=model.actor,
    )


class IngestPipeline:
    def __init__(
        self,
        store: TelemetryStore,
        *,
        mapping: FieldMapping | None = None,
        defaults: SourceDefaults | None = None,
        policy: IngestPolicy | None = None,
    ) -> None:
        self._store = store
        self._mapping = mapping or FieldMapping()
        self._defaults = defaults or SourceDefaults()
        self._policy = policy or IngestPolicy()

    def ingest_objects(
        self,
        source: str,
        kind: str,
        objects: Iterable[tuple[dict[str, Any] | None, str, str]],
        *,
        cursor: Mapping[str, Any] | None = None,
        batch_id: str | None = None,
        received: datetime | None = None,
    ) -> IngestResult:
        now = received or datetime.now(UTC)
        records: list[LogRecord] = []
        rejected: list[tuple[str, str]] = []
        for obj, reason, raw in objects:
            if obj is None:
                rejected.append((reason, redact_text(raw)[:MAX_DEAD_LETTER_CHARS]))
                continue
            try:
                records.append(normalise(obj, mapping=self._mapping, defaults=self._defaults,
                                         received=now))  # fmt: skip
            except RejectedRecord as exc:
                rejected.append((str(exc), redact_text(raw)[:MAX_DEAD_LETTER_CHARS]))
        stored = self._store.write_batch(
            source, kind, records, rejected,
            keep=self._policy.keep_raw, cursor=cursor, batch_id=batch_id,
        )  # fmt: skip
        if not stored:
            return IngestResult(0, 0, duplicate=True)
        return IngestResult(len(records), len(rejected))

    def ingest_lines(
        self, source: str, kind: str, lines: Iterable[str], **kwargs: Any
    ) -> IngestResult:
        return self.ingest_objects(source, kind, parse_json_lines(lines), **kwargs)

    def ingest_otlp(self, source: str, payload: Mapping[str, Any], **kwargs: Any) -> IngestResult:
        objects = ((record, "", json.dumps(record)[:MAX_DEAD_LETTER_CHARS])
                   for record in flatten_otlp_logs(payload))  # fmt: skip
        return self.ingest_objects(source, "otlp", objects, **kwargs)

    def ingest_changes(self, lines: Iterable[str]) -> IngestResult:
        accepted = rejected = 0
        for obj, _reason, _raw in parse_json_lines(lines):
            if obj is None:
                rejected += 1
                continue
            try:
                change = parse_change(obj)
            except RejectedRecord:
                rejected += 1
                continue
            accepted += int(self._store.record_change(change))
        return IngestResult(accepted, rejected)
