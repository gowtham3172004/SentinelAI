"""Durable job queue and transactional outbox on PostgreSQL (P0-09, ADR-003, ADR-022).

- ``enqueue`` and ``emit`` take the caller's connection and run inside the caller's
  transaction, so a state change, the job it triggers and the event it publishes commit
  together or not at all.
- ``claim`` leases one job using ``FOR UPDATE SKIP LOCKED``: concurrent workers never claim
  the same job. Workers extend leases with ``heartbeat``. ``reap`` requeues jobs whose lease
  expired, or marks them ``dead`` and emits a ``job.dead`` alert event.
- Outbox dispatch is at least once. Consumers deduplicate by ``event_id``. Each emitted
  event also sends ``NOTIFY sentinel_outbox`` (delivered only on commit) so dispatchers
  wake without polling.
- Job payloads carry identifiers only, never tenant content (01 section 4): enforced by
  ``validate_payload``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from sentinelai.adapters.redaction import redact

NOTIFY_CHANNEL = "sentinel_outbox"
MAX_PAYLOAD_BYTES = 2048
MAX_ERROR_CHARS = 2000


class PayloadError(ValueError):
    """A job payload contained something other than flat identifiers."""


def validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only a flat mapping of short scalar identifiers (str, int, bool, UUID, None)."""
    clean: dict[str, Any] = {}
    for key, raw in payload.items():
        if not isinstance(key, str) or not key.isidentifier():
            raise PayloadError(f"invalid payload key {key!r}")
        value = str(raw) if isinstance(raw, UUID) else raw
        if value is not None and not isinstance(value, (str, int, bool)):
            raise PayloadError(f"payload value for {key!r} must be a scalar identifier")
        if isinstance(value, str) and len(value) > 200:
            raise PayloadError(f"payload value for {key!r} is too long for an identifier")
        clean[key] = value
    if len(json.dumps(clean)) > MAX_PAYLOAD_BYTES:
        raise PayloadError("payload too large")
    return clean


@dataclass(frozen=True)
class Job:
    id: int
    queue: str
    kind: str
    payload: dict[str, Any]
    tenant_id: UUID | None
    attempts: int
    max_attempts: int
    locked_by: str
    locked_until: datetime


@dataclass(frozen=True)
class OutboxEvent:
    id: int
    event_id: UUID
    tenant_id: UUID | None
    event_type: str
    aggregate: str
    aggregate_id: str
    payload: dict[str, Any]


# --------------------------------------------------------------------------- jobs


def enqueue(
    conn: psycopg.Connection,
    queue: str,
    kind: str,
    payload: Mapping[str, Any],
    *,
    tenant_id: UUID | None = None,
    dedupe_key: str | None = None,
    priority: int = 0,
    delay_s: float = 0,
    max_attempts: int = 5,
) -> int | None:
    """Insert a job; returns its id, or ``None`` if an active job has the same dedupe key."""
    row = conn.execute(
        """
        INSERT INTO jobs (tenant_id, queue, kind, payload, priority, run_after, max_attempts,
                          dedupe_key)
        VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s), %s, %s)
        ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING
        RETURNING id
        """,
        (
            tenant_id,
            queue,
            kind,
            Jsonb(validate_payload(payload)),
            priority,
            delay_s,
            max_attempts,
            dedupe_key,
        ),
    ).fetchone()
    return None if row is None else int(row[0])


_CLAIM = """
UPDATE jobs
   SET status = 'running', locked_by = %(worker)s, attempts = attempts + 1,
       locked_until = now() + make_interval(secs => %(lease)s)
 WHERE id = (SELECT id FROM jobs
              WHERE status = 'queued' AND queue = %(queue)s AND run_after <= now()
              ORDER BY priority DESC, run_after, id
              FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, queue, kind, payload, tenant_id, attempts, max_attempts, locked_by, locked_until
"""


def claim(conn: psycopg.Connection, queue: str, worker: str, lease_s: float = 60) -> Job | None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(_CLAIM, {"worker": worker, "lease": lease_s, "queue": queue}).fetchone()
    return None if row is None else Job(**row)


def heartbeat(conn: psycopg.Connection, job_id: int, worker: str, lease_s: float = 60) -> bool:
    """Extend the lease; ``False`` means the job is no longer ours (reaped or finished)."""
    result = conn.execute(
        "UPDATE jobs SET locked_until = now() + make_interval(secs => %s) "
        "WHERE id = %s AND locked_by = %s AND status = 'running'",
        (lease_s, job_id, worker),
    )
    return result.rowcount == 1


def complete(conn: psycopg.Connection, job_id: int, worker: str) -> bool:
    result = conn.execute(
        "UPDATE jobs SET status = 'done', locked_by = NULL, locked_until = NULL "
        "WHERE id = %s AND locked_by = %s AND status = 'running'",
        (job_id, worker),
    )
    return result.rowcount == 1


def fail(
    conn: psycopg.Connection, job_id: int, worker: str, error: str, *, retry_in_s: float = 10
) -> str | None:
    """Record a failure: requeue with a delay, or mark ``dead`` when attempts are exhausted.

    Returns the new status, or ``None`` if the job was no longer ours.
    """
    row = conn.execute(
        """
        UPDATE jobs
           SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
               run_after = now() + make_interval(secs => %s),
               locked_by = NULL, locked_until = NULL, last_error = %s
         WHERE id = %s AND locked_by = %s AND status = 'running'
        RETURNING status, queue, kind, tenant_id
        """,
        (retry_in_s, redact(error)[:MAX_ERROR_CHARS], job_id, worker),
    ).fetchone()
    if row is None:
        return None
    status, queue, kind, tenant_id = row
    if status == "dead":
        _emit_dead(conn, job_id, queue, kind, tenant_id)
    return str(status)


def reap(conn: psycopg.Connection) -> tuple[int, int]:
    """Requeue expired leases, or mark them dead. Returns (requeued, dead)."""
    rows = conn.execute(
        """
        UPDATE jobs
           SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
               locked_by = NULL, locked_until = NULL,
               last_error = COALESCE(last_error, 'lease expired')
         WHERE id IN (SELECT id FROM jobs
                       WHERE status = 'running' AND locked_until < now()
                       FOR UPDATE SKIP LOCKED)
        RETURNING id, status, queue, kind, tenant_id
        """
    ).fetchall()
    dead = [r for r in rows if r[1] == "dead"]
    for job_id, _, queue, kind, tenant_id in dead:
        _emit_dead(conn, job_id, queue, kind, tenant_id)
    return len(rows) - len(dead), len(dead)


def _emit_dead(
    conn: psycopg.Connection, job_id: int, queue: str, kind: str, tenant_id: UUID | None
) -> None:
    emit(
        conn,
        "job.dead",
        "job",
        str(job_id),
        {"queue": queue, "kind": kind},
        tenant_id=tenant_id,
    )


# --------------------------------------------------------------------------- outbox


def emit(
    conn: psycopg.Connection,
    event_type: str,
    aggregate: str,
    aggregate_id: str,
    payload: Mapping[str, Any],
    *,
    tenant_id: UUID | None = None,
) -> UUID:
    """Write an event in the caller's transaction and NOTIFY dispatchers (sent on commit)."""
    row = conn.execute(
        "INSERT INTO outbox_events (tenant_id, event_type, aggregate, aggregate_id, payload) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id, event_id",
        (tenant_id, event_type, aggregate, aggregate_id, Jsonb(validate_payload(payload))),
    ).fetchone()
    if row is None:  # pragma: no cover - INSERT ... RETURNING always yields a row
        raise RuntimeError("outbox insert returned no row")
    conn.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, str(row[0])))
    event_id: UUID = row[1]
    return event_id


def dispatch_batch(
    conn: psycopg.Connection, publish: Callable[[OutboxEvent], None], limit: int = 100
) -> int:
    """Publish pending events in id order, then mark them dispatched, in one transaction.

    If ``publish`` raises, the transaction rolls back and every event in the batch stays
    pending (at-least-once). Consumers must deduplicate by ``event_id``.
    """
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, event_id, tenant_id, event_type, aggregate, aggregate_id, payload "
            "FROM outbox_events WHERE dispatched_at IS NULL ORDER BY id "
            "FOR UPDATE SKIP LOCKED LIMIT %s",
            (limit,),
        ).fetchall()
        for row in rows:
            publish(OutboxEvent(**row))
        if rows:
            cur.execute(
                "UPDATE outbox_events SET dispatched_at = now() WHERE id = ANY(%s)",
                ([r["id"] for r in rows],),
            )
    return len(rows)


# --------------------------------------------------------------------------- worker


Handler = Callable[[Job], None]


class Worker:
    """Claims and runs jobs until ``stop`` is set; heartbeats while a handler runs.

    Graceful shutdown (NFR-13): setting ``stop`` prevents new claims; the current job
    finishes (or fails) normally and its lease is released.
    """

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        queue: str,
        worker_id: str,
        handler: Handler,
        *,
        lease_s: float = 60,
        poll_s: float = 1.0,
        retry_in_s: float = 10,
    ) -> None:
        self._connect = connect
        self._queue = queue
        self._worker_id = worker_id
        self._handler = handler
        self._lease_s = lease_s
        self._poll_s = poll_s
        self._retry_in_s = retry_in_s

    def run(self, stop: threading.Event) -> int:
        processed = 0
        with self._connect() as conn:
            conn.autocommit = True
            while not stop.is_set():
                job = claim(conn, self._queue, self._worker_id, self._lease_s)
                if job is None:
                    stop.wait(self._poll_s)
                    continue
                self._run_one(conn, job)
                processed += 1
        return processed

    def _run_one(self, conn: psycopg.Connection, job: Job) -> None:
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job, done), daemon=True)
        beat.start()
        try:
            self._handler(job)
        except Exception as exc:
            done.set()
            beat.join()
            fail(conn, job.id, self._worker_id, f"{type(exc).__name__}: {exc}",
                 retry_in_s=self._retry_in_s)  # fmt: skip
            return
        done.set()
        beat.join()
        complete(conn, job.id, self._worker_id)

    def _heartbeat(self, job: Job, done: threading.Event) -> None:
        interval = max(self._lease_s / 3, 0.05)
        with self._connect() as conn:
            conn.autocommit = True
            while not done.wait(interval):
                if not heartbeat(conn, job.id, self._worker_id, self._lease_s):
                    return
