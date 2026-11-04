"""Ingestion and incident management against PostgreSQL, acting as the telemetry role."""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from sentinelai.adapters.db.telemetry_store import INVESTIGATION_QUEUE, PgTelemetryStore
from sentinelai.domain.detection import WINDOW_S
from sentinelai.domain.telemetry import SourceDefaults
from sentinelai.services.incidents import IncidentManager
from sentinelai.services.ingest import IngestPipeline, IngestPolicy

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
ROLE = "r_telemetry"


@pytest.fixture
def tenant(db_url: str) -> uuid.UUID:
    with psycopg.connect(db_url, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO tenants (slug, name) VALUES (%s, 'x') RETURNING id",
            ("tp-" + secrets.token_hex(5),),
        ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


@pytest.fixture
def store(db_url: str, tenant: uuid.UUID) -> Iterator[PgTelemetryStore]:
    with psycopg.connect(db_url) as conn:
        yield PgTelemetryStore(conn, tenant, role=ROLE)


@pytest.fixture
def admin_in(db_url: str, tenant: uuid.UUID) -> Iterator[psycopg.Connection]:
    """Reads exactly what the tenant can see: r_readonly under RLS (a superuser would bypass it)."""
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("SET ROLE r_readonly")
        conn.execute("SELECT set_config('sentinel.tenant_id', %s, false)", (str(tenant),))
        yield conn


def access(
    ts: datetime, route: str, status: int, ms: float = 12.0, trace: str | None = None
) -> str:
    return json.dumps({"ts": ts.isoformat(), "level": "info", "message": f"POST {route} {status}",
                       "service.name": "shop-api", "http.method": "POST", "http.route": route,
                       "http.status_code": status, "duration_ms": ms,
                       "trace_id": trace or secrets.token_hex(16)})  # fmt: skip


def failure(ts: datetime, trace: str) -> str:
    stack = ('Traceback (most recent call last):\n  File "/app/shop/orders/normalize.py", line 10, '
             "in normalize_customer_note\nAttributeError: 'NoneType' object has no attribute "
             "'strip'\n")  # fmt: skip
    return json.dumps({"ts": ts.isoformat(), "level": "error", "message": "unhandled error",
                       "service.name": "shop-api", "trace_id": trace,
                       "exception.type": "AttributeError",
                       "exception.message": "'NoneType' object has no attribute 'strip'",
                       "exception.stacktrace": stack})  # fmt: skip


def traffic(start: datetime, seconds: int, per_s: int, error_every: int | None) -> list[str]:
    """``per_s`` POST /orders per second; every ``error_every``-th fails with a 500 + exception."""
    lines, n = [], 0
    for second in range(seconds):
        for k in range(per_s):
            ts = start + timedelta(seconds=second, milliseconds=k * (1000 // per_s))
            n += 1
            if error_every and n % error_every == 0:
                trace = secrets.token_hex(16)
                lines += [failure(ts, trace), access(ts, "/orders", 500, trace=trace)]
            else:
                lines.append(access(ts, "/orders", 201))
    return lines


def pipeline(store: PgTelemetryStore, sample: float = 0.0) -> IngestPipeline:
    defaults = SourceDefaults(service="shop-api", source_roots=("shop",), app_roots=("/app",))
    return IngestPipeline(store, defaults=defaults, policy=IngestPolicy(info_sample_rate=sample))


def one(conn: psycopg.Connection, query: str, *params: Any) -> Any:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0] if len(row) == 1 else row


# --------------------------------------------------------------------------- ingestion


@pytest.mark.at("AT-ING-01")
def test_batches_are_idempotent_and_windows_add_up(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    lines = traffic(T0, 20, 5, error_every=4)  # 100 requests, 25 failures
    first = pipeline(store).ingest_lines("http:collector", "http", lines, batch_id="b-1")
    again = pipeline(store).ingest_lines("http:collector", "http", lines, batch_id="b-1")
    assert (first.accepted, first.duplicate) == (125, False)
    assert again.duplicate
    totals = one(admin_in, "SELECT sum(requests), sum(errors_5xx) FROM request_windows")
    assert totals == (100, 25)  # the re-delivered batch was not counted twice
    assert one(admin_in, "SELECT count(*) FROM request_windows") == 20 // WINDOW_S
    histogram_total = one(admin_in, "SELECT sum(x) FROM request_windows, unnest(latency) AS x")
    assert histogram_total == 100  # every request landed in exactly one latency bucket


def test_out_of_order_batches_merge_first_and_last_seen(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    late = traffic(T0 + timedelta(minutes=5), 4, 5, error_every=2)
    early = traffic(T0, 4, 5, error_every=2)
    pipeline(store).ingest_lines("file:a", "file", late, cursor={"offset": 100})
    pipeline(store).ingest_lines("file:a", "file", early, cursor={"offset": 200})
    first, last, n = one(admin_in, "SELECT first_seen, last_seen, occurrences FROM fingerprints")
    assert first < T0 + timedelta(seconds=5)
    assert last >= T0 + timedelta(minutes=5)
    assert n == 20
    assert store.source_cursor("file:a", "file") == {"offset": 200}


def test_raw_storage_keeps_problems_and_samples_the_rest(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    pipeline(store, sample=0.0).ingest_lines("http:x", "http", traffic(T0, 10, 10, error_every=10))
    kept = one(
        admin_in,
        "SELECT count(*) FILTER (WHERE level = 'error'), "
        "count(*) FILTER (WHERE http_status = 500), count(*) FROM log_events",
    )
    assert kept == (10, 10, 20)  # all errors and 5xx kept; healthy info records not stored
    assert one(admin_in, "SELECT sum(requests) FROM request_windows") == 100  # but all counted


@pytest.mark.at("AT-ING-04")
def test_bad_lines_go_to_dead_letters_redacted(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    lines = ["not json password=hunter2pass", "[1, 2]", json.dumps({"msg": "no service"})]
    result = IngestPipeline(store).ingest_lines("http:y", "http", lines)
    assert (result.accepted, result.rejected) == (0, 3)
    raws = [r[0] for r in admin_in.execute("SELECT raw FROM dead_letters").fetchall()]
    assert len(raws) == 3
    assert not any("hunter2pass" in raw for raw in raws)


def test_release_events_are_idempotent(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    event = json.dumps({"kind": "deploy", "service": "shop-api", "version": "v1.1.0",
                        "occurred_at": T0.isoformat(), "idempotency_key": "k1",
                        "git_sha": "3970ecac1bf7099605ed6cef92c5155b8ef6aa44"})  # fmt: skip
    bad = json.dumps({"kind": "deploy", "service": "shop-api"})
    assert IngestPipeline(store).ingest_changes([event, event, bad]).accepted == 1
    assert one(admin_in, "SELECT count(*) FROM release_events") == 1
    assert one(admin_in, "SELECT details->>'git_sha' FROM release_events")[:8] == "3970ecac"


@pytest.mark.at("AT-TEN-01")
def test_store_never_sees_another_tenants_telemetry(db_url: str, store: PgTelemetryStore) -> None:
    pipeline(store).ingest_lines("http:z", "http", traffic(T0, 10, 3, error_every=2))
    with psycopg.connect(db_url, autocommit=True) as conn:
        other_id = one(conn, "INSERT INTO tenants (slug, name) VALUES (%s, 'o') RETURNING id",
                       "to-" + secrets.token_hex(4))  # fmt: skip
    with psycopg.connect(db_url) as conn:
        other = PgTelemetryStore(conn, other_id, role=ROLE)
        with other.transaction() as tx:
            assert tx.services() == []
        manager = IncidentManager(other)
        assert manager.tick(T0 + timedelta(seconds=15)).opened == []


# --------------------------------------------------------------------------- incidents


def ingest_story(store: PgTelemetryStore, onset: datetime) -> None:
    """An hour of healthy traffic, a deploy, then 25% failures from ``onset``."""
    lines = traffic(onset - timedelta(minutes=60), 3540, 1, error_every=None)
    pipeline(store).ingest_lines("http:story", "http", lines)
    deploy = json.dumps({"kind": "deploy", "service": "shop-api", "version": "v1.1.0",
                         "occurred_at": (onset - timedelta(minutes=2)).isoformat(),
                         "idempotency_key": "deploy-v1.1.0"})  # fmt: skip
    IngestPipeline(store).ingest_changes([deploy])
    pipeline(store).ingest_lines("http:story", "http", traffic(onset, 90, 4, error_every=4))


@pytest.mark.at("AT-INC-01")
def test_incident_opens_once_with_job_event_and_audit(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    onset = T0 + timedelta(hours=1)
    ingest_story(store, onset)
    manager = IncidentManager(store)
    at = onset + timedelta(seconds=60)
    first = manager.tick(at)
    assert len(first.opened) == 1
    public_id = first.opened[0]
    assert manager.tick(at).opened == []  # same instant again: idempotent, nothing duplicated
    row = one(
        admin_in,
        "SELECT severity::text, class_prior::text, code_patch_candidate, "
        "suspected_release_id IS NOT NULL, title, signal_count FROM incidents",
    )
    assert row[1:5] == ("regression_from_release", True, True, "AttributeError on /orders")
    assert row[0] in ("sev2", "sev3")
    signals_after_two_ticks = row[5]
    assert one(admin_in, "SELECT count(*) FROM incident_signals") == signals_after_two_ticks
    job = one(admin_in, "SELECT queue, kind, dedupe_key FROM jobs WHERE tenant_id = %s",
              store._tenant)  # fmt: skip
    assert job[0:2] == (INVESTIGATION_QUEUE, "investigate")
    assert one(admin_in, "SELECT count(*) FROM outbox_events WHERE event_type = 'incident.created' "
                         "AND payload->>'public_id' = %s", public_id) == 1  # fmt: skip
    assert (
        one(admin_in, "SELECT action FROM audit_log WHERE entity_id = %s", public_id)
        == "incident.opened"
    )

    later = manager.tick(at + timedelta(seconds=20))
    assert later.attached == [public_id]  # continuing failure attaches, no new incident
    assert one(admin_in, "SELECT count(*) FROM incidents") == 1


@pytest.mark.at("AT-INC-05")
def test_quiet_incident_self_resolves_and_recurrence_reopens(
    store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    onset = T0 + timedelta(hours=1)
    ingest_story(store, onset)
    manager = IncidentManager(store)
    opened = manager.tick(onset + timedelta(seconds=60)).opened
    quiet_at = onset + timedelta(seconds=60) + timedelta(minutes=16)
    resolved = manager.tick(quiet_at)
    assert resolved.resolved == opened
    assert one(admin_in, "SELECT status::text FROM incidents") == "resolved"

    again = quiet_at + timedelta(minutes=5)
    pipeline(store).ingest_lines(
        "http:story", "http", traffic(again - timedelta(seconds=60), 60, 4, 4)
    )
    reopened = manager.tick(again)
    assert reopened.reopened == opened
    assert reopened.opened == []
    assert one(admin_in, "SELECT status::text, reopened_count FROM incidents") == ("detected", 1)


@pytest.mark.at("AT-INC-04")
def test_known_noise_fingerprints_are_suppressed(
    db_url: str, store: PgTelemetryStore, admin_in: psycopg.Connection
) -> None:
    onset = T0 + timedelta(hours=1)
    pipeline(store).ingest_lines(
        "http:n", "http", traffic(onset - timedelta(minutes=30), 1790, 1, None)
    )
    lines = [
        line
        for line in traffic(onset, 90, 4, error_every=4)
        if json.loads(line).get("level") == "error"
    ]  # exceptions only, requests all fine
    pipeline(store).ingest_lines("http:n", "http", lines)
    fp = one(admin_in, "SELECT fingerprint FROM fingerprints")
    service = one(admin_in, "SELECT id FROM services")
    with psycopg.connect(db_url) as conn, conn.transaction():
        conn.execute("SELECT set_config('sentinel.tenant_id', %s, true)", (str(store._tenant),))
        conn.execute("SET LOCAL ROLE r_api")
        conn.execute("INSERT INTO noise_rules (tenant_id, service_id, fingerprint, reason) "
                     "VALUES (%s, %s, %s, 'known')", (store._tenant, service, fp))  # fmt: skip
    result = IncidentManager(store).tick(onset + timedelta(seconds=60))
    assert result.opened == []
    assert result.suppressed >= 1
    assert one(
        admin_in, "SELECT bool_and(suppressed AND incident_id IS NULL) FROM incident_signals"
    )
