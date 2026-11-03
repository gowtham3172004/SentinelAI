"""AT-Q-01..04: job queue and transactional outbox, run as the orchestrator role."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest

from sentinelai.adapters.db import queue as q
from sentinelai.adapters.redaction import register_secret

ROLE = "-c role=r_orch"


@pytest.fixture
def connect(db_url: str) -> Callable[[], psycopg.Connection]:
    def factory() -> psycopg.Connection:
        return psycopg.connect(db_url, options=ROLE, autocommit=True)

    return factory


@pytest.fixture
def conn(connect: Callable[[], psycopg.Connection]) -> Iterator[psycopg.Connection]:
    with connect() as c:
        yield c


def unique_queue() -> str:
    return "test-" + uuid.uuid4().hex[:10]


# --------------------------------------------------------------------------- AT-Q-01


@pytest.mark.at("AT-Q-01")
def test_concurrent_workers_never_double_claim(
    conn: psycopg.Connection, connect: Callable[[], psycopg.Connection]
) -> None:
    queue_name, total, workers = unique_queue(), 10_000, 16
    with conn.transaction():
        for n in range(total):
            q.enqueue(conn, queue_name, "noop", {"n": n})
    claimed: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(workers)

    def work(index: int) -> None:
        with connect() as c:
            start.wait()
            while (job := q.claim(c, queue_name, f"w{index}")) is not None:
                assert q.complete(c, job.id, f"w{index}")
                with lock:
                    claimed.append(job.id)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert len(claimed) == total
    assert len(set(claimed)) == total  # no job claimed twice
    remaining = conn.execute(
        "SELECT count(*) FROM jobs WHERE queue = %s AND status <> 'done'", (queue_name,)
    ).fetchone()
    assert remaining == (0,)


def test_claim_respects_priority_and_delay(conn: psycopg.Connection) -> None:
    name = unique_queue()
    low = q.enqueue(conn, name, "k", {"p": "low"}, priority=0)
    high = q.enqueue(conn, name, "k", {"p": "high"}, priority=5)
    q.enqueue(conn, name, "k", {"p": "later"}, priority=9, delay_s=3600)
    first, second = q.claim(conn, name, "w"), q.claim(conn, name, "w")
    assert first is not None
    assert second is not None
    assert (first.id, second.id) == (high, low)
    assert q.claim(conn, name, "w") is None  # the delayed job is not due


# --------------------------------------------------------------------------- AT-Q-02


@pytest.mark.at("AT-Q-02")
def test_expired_lease_is_requeued_then_dead_with_alert(conn: psycopg.Connection) -> None:
    name = unique_queue()
    job_id = q.enqueue(conn, name, "flaky", {"run": "x"}, max_attempts=2)
    assert job_id is not None
    for attempt in (1, 2):
        job = q.claim(conn, name, "crashed-worker", lease_s=0.05)
        assert job is not None
        assert job.attempts == attempt
        time.sleep(0.2)  # the worker "dies" and the lease expires
        requeued, dead = q.reap(conn)
        assert (requeued, dead) == ((1, 0) if attempt == 1 else (0, 1))
        assert not q.heartbeat(conn, job_id, "crashed-worker")  # the lease is gone
    status = conn.execute("SELECT status, last_error FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert status == ("dead", "lease expired")
    alert = conn.execute(
        "SELECT payload FROM outbox_events WHERE event_type = 'job.dead' AND aggregate_id = %s",
        (str(job_id),),
    ).fetchone()
    assert alert == ({"queue": name, "kind": "flaky"},)


@pytest.mark.at("AT-Q-02")
def test_handler_failure_retries_then_dies_and_errors_are_redacted(
    conn: psycopg.Connection,
) -> None:
    secret = "tok-" + uuid.uuid4().hex
    register_secret(secret)
    name = unique_queue()
    job_id = q.enqueue(conn, name, "boom", {}, max_attempts=2)
    assert job_id is not None
    for expected in ("queued", "dead"):
        job = q.claim(conn, name, "w")
        assert job is not None
        assert q.fail(conn, job.id, "w", f"failed with {secret}", retry_in_s=0) == expected
    row = conn.execute("SELECT last_error FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert row is not None
    assert secret not in row[0]
    assert q.fail(conn, job_id, "w", "late") is None  # not ours any more


@pytest.mark.at("AT-Q-02")
def test_worker_runs_heartbeats_and_stops_gracefully(
    conn: psycopg.Connection, connect: Callable[[], psycopg.Connection]
) -> None:
    name = unique_queue()
    ok, bad = q.enqueue(conn, name, "ok", {}), q.enqueue(conn, name, "bad", {}, max_attempts=1)
    stop = threading.Event()
    seen: list[str] = []

    def handler(job: q.Job) -> None:
        time.sleep(0.3)  # longer than the lease: only heartbeats keep it alive
        seen.append(job.kind)
        if job.kind == "bad":
            raise RuntimeError("handler error")
        if len(seen) == 2:
            stop.set()

    worker = q.Worker(connect, name, "w1", handler, lease_s=0.15, poll_s=0.05, retry_in_s=0)
    thread = threading.Thread(target=lambda: worker.run(stop))
    thread.start()
    deadline = time.monotonic() + 30
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert q.reap(conn) == (0, 0)  # heartbeats kept the leases valid while running
    statuses: dict[int, str] = dict(
        conn.execute("SELECT id, status FROM jobs WHERE id = ANY(%s)", ([ok, bad],)).fetchall()
    )
    assert statuses == {ok: "done", bad: "dead"}


# --------------------------------------------------------------------------- AT-Q-03


@pytest.mark.at("AT-Q-03")
def test_dedupe_key_blocks_duplicates_while_active(conn: psycopg.Connection) -> None:
    name, key = unique_queue(), "investigate:" + uuid.uuid4().hex
    first = q.enqueue(conn, name, "investigate", {}, dedupe_key=key)
    assert first is not None
    assert q.enqueue(conn, name, "investigate", {}, dedupe_key=key) is None
    job = q.claim(conn, name, "w")
    assert job is not None
    assert q.enqueue(conn, name, "investigate", {}, dedupe_key=key) is None  # still running
    q.complete(conn, job.id, "w")
    assert q.enqueue(conn, name, "investigate", {}, dedupe_key=key) is not None  # finished


def test_state_change_and_job_commit_atomically(conn: psycopg.Connection) -> None:
    name = unique_queue()

    def change_state_then_fail() -> None:
        with conn.transaction():
            q.enqueue(conn, name, "k", {})
            q.emit(conn, "incident.created", "incident", "x", {})
            raise RuntimeError("state change failed")

    with pytest.raises(RuntimeError):
        change_state_then_fail()
    jobs = conn.execute("SELECT count(*) FROM jobs WHERE queue = %s", (name,)).fetchone()
    assert jobs == (0,)


@pytest.mark.parametrize(
    "payload",
    [{"nested": {"a": 1}}, {"text": "x" * 500}, {"bad key": 1}, {"items": [1, 2]}, {"f": 1.5}],
)
def test_payloads_carry_identifiers_only(payload: dict[str, object]) -> None:
    with pytest.raises(q.PayloadError):
        q.validate_payload(payload)
    ok = q.validate_payload({"incident_id": uuid.uuid4(), "attempt": 2, "force": False})
    assert isinstance(ok["incident_id"], str)


# --------------------------------------------------------------------------- AT-Q-04


@pytest.mark.at("AT-Q-04")
def test_outbox_is_at_least_once_and_consumers_deduplicate(
    conn: psycopg.Connection, connect: Callable[[], psycopg.Connection]
) -> None:
    with conn.transaction():
        conn.execute("UPDATE outbox_events SET dispatched_at = now() WHERE dispatched_at IS NULL")
    ids = [q.emit(conn, "run.state_changed", "run", f"r{n}", {"n": n}) for n in range(3)]

    def crash(_event: q.OutboxEvent) -> None:
        raise ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        q.dispatch_batch(conn, crash)
    delivered: list[uuid.UUID] = []
    assert q.dispatch_batch(conn, lambda e: delivered.append(e.event_id)) == 3
    assert delivered == ids  # in order, nothing lost after the failed attempt

    # Crash after publishing but before marking: events are delivered again.
    q.emit(conn, "run.state_changed", "run", "r3", {})
    seen: set[uuid.UUID] = set()
    received: list[uuid.UUID] = []

    def consumer(event: q.OutboxEvent) -> None:
        received.append(event.event_id)
        seen.add(event.event_id)  # idempotent consumer

    with connect() as other:
        other.autocommit = False
        with other.cursor() as cur:
            rows = cur.execute(
                "SELECT event_id FROM outbox_events WHERE dispatched_at IS NULL"
            ).fetchall()
        for (event_id,) in rows:
            consumer(q.OutboxEvent(0, event_id, None, "x", "run", "r3", {}))
        other.rollback()  # "crashed" before marking dispatched
    assert q.dispatch_batch(conn, consumer) == 1
    assert len(received) == 2
    assert len(seen) == 1


@pytest.mark.at("AT-Q-04")
def test_notify_is_delivered_only_on_commit(
    conn: psycopg.Connection, connect: Callable[[], psycopg.Connection]
) -> None:
    with connect() as listener:
        listener.execute(f"LISTEN {q.NOTIFY_CHANNEL}")
        writer = connect()
        writer.autocommit = False
        q.emit(writer, "incident.created", "incident", "n1", {})
        assert list(listener.notifies(timeout=0.5, stop_after=1)) == []  # not committed yet
        writer.commit()
        notes = list(listener.notifies(timeout=5, stop_after=1))
        writer.close()
    assert len(notes) == 1
    assert notes[0].channel == q.NOTIFY_CHANNEL
