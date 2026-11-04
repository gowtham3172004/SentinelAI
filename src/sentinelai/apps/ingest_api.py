"""HTTP ingestion service: ``sentinelai serve ingest`` (sentinel-telemetry, D2 section 14.3).

Endpoints, chosen so any production stack can send telemetry without custom code:
- ``POST /v1/ingest/logs?source=NAME``: NDJSON or a JSON array of log objects (Vector, Fluent
  Bit, Logstash, custom shippers). The batch id is the ``Idempotency-Key`` header if sent,
  otherwise the body hash, so a retried identical batch is never counted twice.
- ``POST /v1/otlp/logs``: OTLP/HTTP JSON from any OpenTelemetry Collector or SDK. The batch id
  is the body hash, so retried identical payloads are ignored.
- ``POST /v1/events/releases``: deploy/config events from CI/CD, signed with HMAC-SHA256 over
  ``"{timestamp}.{body}"`` (headers ``X-Sentinel-Timestamp`` and ``X-Sentinel-Signature:
  sha256=<hex>``), with a 5-minute replay window.

Fails closed: without an ingest token (and webhook secret for releases) the endpoints refuse.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import SecretStr

from sentinelai import __version__
from sentinelai.adapters.db.telemetry_store import PgTelemetryStore
from sentinelai.config.target import TargetConfig
from sentinelai.domain.telemetry import RejectedRecord
from sentinelai.services.ingest import IngestPipeline, IngestPolicy, parse_change, parse_json_lines

MAX_BODY_BYTES = 10 * 1024 * 1024
REPLAY_WINDOW_S = 300
SOURCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"


def _secret_ok(provided: str | None, expected: SecretStr | None) -> bool:
    if expected is None or not expected.get_secret_value() or provided is None:
        return False
    return hmac.compare_digest(provided.encode(), expected.get_secret_value().encode())


def verify_release_signature(
    body: bytes, timestamp: str | None, signature: str | None, secret: SecretStr | None,
    *, now: float | None = None,
) -> None:  # fmt: skip
    if secret is None or not secret.get_secret_value():
        raise HTTPException(503, "release webhook secret not configured")
    if not timestamp or not signature or not signature.startswith("sha256="):
        raise HTTPException(401, "missing signature")
    try:
        sent = int(timestamp)
    except ValueError:
        raise HTTPException(401, "bad timestamp") from None
    if abs((now or time.time()) - sent) > REPLAY_WINDOW_S:
        raise HTTPException(401, "stale or future timestamp")
    expected = hmac.new(secret.get_secret_value().encode(), f"{sent}.".encode() + body,
                        hashlib.sha256).hexdigest()  # fmt: skip
    if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
        raise HTTPException(401, "bad signature")


def create_ingest_app(
    connect: Callable[[], psycopg.Connection],
    tenant_id: Any,
    *,
    ingest_token: SecretStr | None,
    release_secret: SecretStr | None,
    target: TargetConfig | None = None,
    role: str | None = "r_telemetry",
) -> FastAPI:
    target = target or TargetConfig()
    local = threading.local()
    app = FastAPI(title="sentinel-telemetry", version=__version__)

    @contextmanager
    def store() -> Iterator[PgTelemetryStore]:
        conn: psycopg.Connection | None = getattr(local, "conn", None)
        if conn is None or conn.closed:
            conn = local.conn = connect()
        try:
            yield PgTelemetryStore(conn, tenant_id, role=role)
        except psycopg.OperationalError:
            conn.close()  # reconnect on the next request
            raise HTTPException(503, "database unavailable") from None

    def pipeline(s: PgTelemetryStore) -> IngestPipeline:
        return IngestPipeline(s, mapping=target.field_mapping(), defaults=target.defaults(),
                              policy=IngestPolicy(target.telemetry.info_sample_rate))  # fmt: skip

    def authorise(authorization: str | None) -> None:
        token = (authorization or "").removeprefix("Bearer ").strip() or None
        if not _secret_ok(token, ingest_token):
            raise HTTPException(401, "invalid or missing ingest token")

    async def read_body(request: Request) -> bytes:
        declared = int(request.headers.get("content-length") or 0)
        if declared > MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")
        return body

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        with store() as s, s.transaction() as tx:
            tx.conn.execute("SELECT 1")
        return {"database": "up"}

    @app.post("/v1/ingest/logs")
    async def ingest_logs(
        request: Request,
        source: str = Query(pattern=SOURCE_PATTERN),
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorise(authorization)
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise HTTPException(400, "Idempotency-Key must be 1-128 characters")
        raw = await read_body(request)
        batch_id = idempotency_key or "body-" + hashlib.sha256(raw).hexdigest()[:40]
        body = raw.decode("utf-8", "replace")
        stripped = body.lstrip()
        if stripped.startswith("["):
            try:
                items = json.loads(stripped)
            except json.JSONDecodeError:
                raise HTTPException(400, "invalid JSON array") from None
            lines = [json.dumps(item) for item in items]
        else:
            lines = body.splitlines()
        with store() as s:
            result = pipeline(s).ingest_objects(f"http:{source}", "http", parse_json_lines(lines),
                                                batch_id=batch_id)  # fmt: skip
        return {"accepted": result.accepted, "rejected": result.rejected,
                "duplicate": result.duplicate}  # fmt: skip

    @app.post("/v1/otlp/logs")
    async def otlp_logs(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorise(authorization)
        body = await read_body(request)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(415, "send OTLP/HTTP with JSON encoding") from None
        batch_id = "otlp-" + hashlib.sha256(body).hexdigest()[:40]
        with store() as s:
            result = pipeline(s).ingest_otlp("otlp:collector", payload, batch_id=batch_id)
        return {"partialSuccess": {"rejectedLogRecords": result.rejected}}

    @app.post("/v1/events/releases")
    async def release_event(
        request: Request,
        x_sentinel_timestamp: str | None = Header(default=None),
        x_sentinel_signature: str | None = Header(default=None),
    ) -> dict[str, Any]:
        body = await read_body(request)
        verify_release_signature(body, x_sentinel_timestamp, x_sentinel_signature, release_secret)
        try:
            change = parse_change(json.loads(body))
        except (json.JSONDecodeError, RejectedRecord) as exc:
            raise HTTPException(422, str(exc)[:300]) from None
        with store() as s:
            created = s.record_change(change)
        return {"recorded": created}

    return app
