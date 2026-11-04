"""Milestone 2 acceptance: Sentinel detects the demo's incidents and tells code, configuration
and infrastructure failures apart, from the demo application's *real* telemetry.

Recorded-mode replay (design/01 F13): the generated repository's actual code runs (baseline,
then the fault) and its real JSON logs are captured. Those request groups are then replayed
over a realistic timeline (a healthy half hour, then 90 s of failure) with fresh trace ids,
together with the matching deploy and configuration events. Nothing in Sentinel knows which
scenario it is looking at.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

import democtl
from sentinelai.adapters.db.telemetry_store import PgTelemetryStore
from sentinelai.config.target import load_target_config
from sentinelai.services.incidents import IncidentManager
from sentinelai.services.ingest import IngestPipeline, IngestPolicy
from tests.demo import conftest as demo_fixtures
from tests.demo.conftest import probe

payment_url = demo_fixtures.payment_url  # reuse the demo's real payment-stub fixture
ONSET = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)
TARGET = load_target_config(Path("demo/app/sentinel.yaml"))
WITH_NOTE = {"customer_id": "c-1", "items": [{"sku": "SKU-MUG", "qty": 1}], "customer_note": "hi"}
WITHOUT_NOTE = {"customer_id": "c-2", "items": [{"sku": "SKU-TEE", "qty": 1}]}
Requests = list[tuple[str, str, dict[str, Any] | None]]

SCENARIO_TRAFFIC: dict[str, Requests] = {
    "S01": [("POST", "/orders", WITH_NOTE), ("POST", "/orders", WITHOUT_NOTE),
            ("GET", "/orders", None)] * 10,
    "S22": [("POST", "/orders", WITH_NOTE), ("POST", "/orders/{last}/pay", None)] * 10,
    "S33": [("POST", "/orders", WITH_NOTE), ("GET", "/orders", None)] * 5,
}  # fmt: skip


def request_groups(logs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group log lines by request (trace id), keeping request order."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in logs:
        if entry.get("trace_id"):
            groups[entry["trace_id"]].append(entry)
    return list(groups.values())


def replay(
    groups: list[list[dict[str, Any]]], start: datetime, seconds: int, per_s: int
) -> list[str]:
    lines = []
    for index in range(seconds * per_s):
        ts = start + timedelta(seconds=index / per_s)
        trace = secrets.token_hex(16)
        for entry in groups[index % len(groups)]:
            lines.append(json.dumps({**entry, "ts": ts.isoformat(), "trace_id": trace}))
    return lines


def event(kind: str, version: str, minutes_before: float, **extra: Any) -> str:
    return json.dumps({"kind": kind, "service": "shop-api", "environment": "production",
                       "version": version, "idempotency_key": str(uuid.uuid4()),
                       "occurred_at": (ONSET - timedelta(minutes=minutes_before)).isoformat(),
                       **extra})  # fmt: skip


@pytest.fixture
def store(db_url: str) -> Iterator[PgTelemetryStore]:
    with psycopg.connect(db_url, autocommit=True) as conn:
        row = conn.execute("INSERT INTO tenants (slug, name) VALUES (%s, 'e2e') RETURNING id",
                           ("e2e-" + secrets.token_hex(4),)).fetchone()  # fmt: skip
    assert row is not None
    with psycopg.connect(db_url) as conn:
        yield PgTelemetryStore(conn, row[0], role="r_telemetry")


def run_story(
    store: PgTelemetryStore, healthy: list[dict[str, Any]], faulty: list[dict[str, Any]] | None,
    events: list[str],
) -> dict[str, Any]:  # fmt: skip
    pipeline = IngestPipeline(store, mapping=TARGET.field_mapping(), defaults=TARGET.defaults(),
                              policy=IngestPolicy(info_sample_rate=0.0))  # fmt: skip
    pipeline.ingest_changes(events)
    healthy_lines = replay(request_groups(healthy), ONSET - timedelta(minutes=30), 1795, 1)
    pipeline.ingest_lines("file:shop-api.jsonl", "file", healthy_lines)
    if faulty is not None:
        pipeline.ingest_lines(
            "file:shop-api.jsonl", "file", replay(request_groups(faulty), ONSET, 90, 4)
        )
    tiers = {"shop-api": TARGET.route_tiers()}
    result = IncidentManager(store, route_tiers=tiers).tick(ONSET + timedelta(seconds=60))
    with store.transaction() as tx:
        rows = tx.conn.execute(
            "SELECT i.public_id, i.severity::text, i.title, i.class_prior::text, "
            "i.class_prior_rule, i.code_patch_candidate, i.class_prior_reasons, r.version "
            "FROM incidents i "
            "LEFT JOIN release_events r ON r.id = i.suspected_release_id"
        ).fetchall()
        jobs = tx.conn.execute("SELECT count(*) FROM jobs WHERE tenant_id = %s",
                               (tx.tenant_id,)).fetchone()  # fmt: skip
    keys = ("id", "severity", "title", "class", "rule", "patch", "reasons", "release")
    return {"opened": result.opened, "incidents": [dict(zip(keys, r, strict=True)) for r in rows],
            "jobs": jobs[0] if jobs else 0}  # fmt: skip


def settings(tmp_path: Path, payment_url: str, **overrides: Any) -> dict[str, Any]:
    return {"database_url": f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}",
            "payment_url": payment_url, "payment_timeout_s": 2.0, **overrides}  # fmt: skip


@pytest.fixture
def ws(tmp_path: Path) -> democtl.Workspace:
    workspace = democtl.Workspace(tmp_path / ".demo")
    democtl.init(workspace)
    return workspace


@pytest.mark.at("AT-DET-02")
def test_healthy_service_opens_no_incident(
    store: PgTelemetryStore, ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    healthy = probe(ws.repo, settings(tmp_path, payment_url, service_version="v1.0.0"),
                    SCENARIO_TRAFFIC["S01"]).logs  # fmt: skip
    story = run_story(store, healthy, None, [event("deploy", "v1.0.0", 180)])
    assert story["opened"] == []
    assert story["incidents"] == []


@pytest.mark.at("AT-DET-04")
def test_s01_code_regression_is_detected_and_attributed_to_the_release(
    store: PgTelemetryStore, ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    requests = SCENARIO_TRAFFIC["S01"]
    healthy = probe(
        ws.repo, settings(tmp_path, payment_url, service_version="v1.0.0"), requests
    ).logs
    democtl.inject(ws, "S01")
    sha, version = democtl.head(ws)
    faulty = probe(ws.repo, settings(tmp_path, payment_url, service_version=version,
                                     git_sha=sha), requests).logs  # fmt: skip
    story = run_story(store, healthy, faulty, [
        event("deploy", "v1.0.0", 180), event("deploy", "v1.1.0", 3, git_sha=sha),
    ])  # fmt: skip
    (incident,) = story["incidents"]
    assert incident["class"] == "regression_from_release"
    assert incident["patch"] is True  # the code pipeline is appropriate
    assert incident["release"] == "v1.1.0"
    assert incident["title"] == "AttributeError on /orders"
    assert incident["severity"] in ("sev1", "sev2")  # tier-0 endpoint
    assert story["jobs"] == 1  # investigation queued


@pytest.mark.at("AT-DET-04")
def test_s22_configuration_fault_is_not_a_code_candidate(
    store: PgTelemetryStore, ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    requests = SCENARIO_TRAFFIC["S22"]
    healthy = probe(
        ws.repo, settings(tmp_path, payment_url, service_version="v1.0.0"), requests
    ).logs
    faulty = probe(ws.repo, settings(tmp_path, payment_url, service_version="v1.0.0",
                                     payment_timeout_s=0.001), requests).logs  # fmt: skip
    story = run_story(store, healthy, faulty, [
        event("deploy", "v1.0.0", 180),
        event("config_change", "v1.0.0", 3, changed_keys=["SHOP_PAYMENT_TIMEOUT_S"]),
    ])  # fmt: skip
    (incident,) = story["incidents"]
    assert incident["class"] == "configuration"
    assert incident["patch"] is False
    assert any("SHOP_PAYMENT_TIMEOUT_S" in reason for reason in incident["reasons"])
    assert "/orders/{order_id}/pay" in incident["title"]


@pytest.mark.at("AT-DET-04")
def test_s33_database_outage_is_infrastructure_not_a_code_candidate(
    store: PgTelemetryStore, ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    requests = SCENARIO_TRAFFIC["S33"]
    healthy = probe(
        ws.repo, settings(tmp_path, payment_url, service_version="v1.0.0"), requests
    ).logs
    outage = democtl.load_scenarios()["S33"].scenario.fault.test_database_url
    faulty = probe(ws.repo, settings(tmp_path, payment_url, service_version="v1.0.0",
                                     database_url=outage), requests).logs  # fmt: skip
    story = run_story(store, healthy, faulty, [event("deploy", "v1.0.0", 180)])
    (incident,) = story["incidents"]
    assert incident["class"] == "infrastructure"
    assert incident["patch"] is False
    assert incident["release"] is None
    assert any("library/driver" in reason for reason in incident["reasons"])
