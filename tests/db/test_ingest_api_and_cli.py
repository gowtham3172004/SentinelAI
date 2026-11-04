"""HTTP ingest service and telemetry CLI against PostgreSQL."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from typer.testing import CliRunner

from sentinelai.apps.ingest_api import create_ingest_app
from sentinelai.cli.main import app as cli
from tests.db.test_telemetry_pipeline import T0, access, traffic

TOKEN = "ingest-" + secrets.token_hex(12)
WEBHOOK = "whsec-" + secrets.token_hex(12)
runner = CliRunner()


@pytest.fixture
def tenant_slug(db_url: str) -> tuple[str, uuid.UUID]:
    slug = "api-" + secrets.token_hex(4)
    with psycopg.connect(db_url, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO tenants (slug, name) VALUES (%s, 'x') RETURNING id", (slug,)
        ).fetchone()
    assert row is not None
    return slug, row[0]


@pytest.fixture
def client(db_url: str, tenant_slug: tuple[str, uuid.UUID]) -> TestClient:
    app = create_ingest_app(
        lambda: psycopg.connect(db_url),
        tenant_slug[1],
        ingest_token=SecretStr(TOKEN),
        release_secret=SecretStr(WEBHOOK),
    )
    return TestClient(app)


def count(db_url: str, tenant: uuid.UUID, query: str) -> int:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("SET ROLE r_readonly")
        conn.execute("SELECT set_config('sentinel.tenant_id', %s, false)", (str(tenant),))
        row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0] or 0)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_logs_endpoint_requires_token_and_valid_source(client: TestClient) -> None:
    body = "\n".join(traffic(T0, 2, 2, None))
    assert client.post("/v1/ingest/logs?source=vector", content=body).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert (
        client.post("/v1/ingest/logs?source=vector", content=body, headers=wrong).status_code == 401
    )
    too_long = {**AUTH, "Idempotency-Key": "k" * 200}
    assert (
        client.post("/v1/ingest/logs?source=vector", content=body, headers=too_long).status_code
        == 400
    )
    assert client.post("/v1/ingest/logs?source=bad%20name", content=body,
                       headers={**AUTH, "Idempotency-Key": "k"}).status_code == 422  # fmt: skip


@pytest.mark.at("AT-ING-01")
def test_ndjson_and_json_array_batches_are_idempotent(
    client: TestClient, db_url: str, tenant_slug: tuple[str, uuid.UUID]
) -> None:
    lines = traffic(T0, 4, 5, error_every=5)
    headers = {**AUTH, "Idempotency-Key": "batch-1"}
    first = client.post("/v1/ingest/logs?source=vector", content="\n".join(lines), headers=headers)
    assert first.json() == {"accepted": 24, "rejected": 0, "duplicate": False}
    retry = client.post("/v1/ingest/logs?source=vector", content="\n".join(lines), headers=headers)
    assert retry.json()["duplicate"] is True
    array = json.dumps(
        [json.loads(line) for line in traffic(T0 + timedelta(minutes=1), 1, 3, None)]
    )
    assert (
        client.post(
            "/v1/ingest/logs?source=vector",
            content=array,
            headers={**AUTH, "Idempotency-Key": "batch-2"},
        ).json()["accepted"]
        == 3
    )
    assert count(db_url, tenant_slug[1], "SELECT sum(requests) FROM request_windows") == 23


def test_batches_without_a_key_are_deduplicated_by_content(
    client: TestClient, db_url: str, tenant_slug: tuple[str, uuid.UUID]
) -> None:
    """Shippers that can only send static headers (Vector, Fluent Bit) still get idempotency."""
    body = "\n".join(traffic(T0, 2, 5, None))
    first = client.post("/v1/ingest/logs?source=fluentbit", content=body, headers=AUTH)
    retry = client.post("/v1/ingest/logs?source=fluentbit", content=body, headers=AUTH)
    assert (first.json()["accepted"], retry.json()["duplicate"]) == (10, True)
    assert count(db_url, tenant_slug[1], "SELECT sum(requests) FROM request_windows") == 10


def test_otlp_endpoint_ingests_and_ignores_retries(
    client: TestClient, db_url: str, tenant_slug: tuple[str, uuid.UUID]
) -> None:
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": str(int(T0.timestamp() * 1e9)),
                                "severityText": "ERROR",
                                "body": {"stringValue": "boom"},
                                "attributes": [
                                    {"key": "exception.type", "value": {"stringValue": "KeyError"}}
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    body = json.dumps(payload)
    assert client.post("/v1/otlp/logs", content=body).status_code == 401
    assert client.post("/v1/otlp/logs", content=body, headers=AUTH).status_code == 200
    assert client.post("/v1/otlp/logs", content=body, headers=AUTH).status_code == 200  # retry
    assert client.post("/v1/otlp/logs", content=b"\x08\x01", headers=AUTH).status_code == 415
    assert count(db_url, tenant_slug[1], "SELECT count(*) FROM log_events") == 1
    assert (
        count(db_url, tenant_slug[1], "SELECT count(*) FROM services WHERE name = 'checkout'") == 1
    )


@pytest.mark.at("AT-REL-EV-01")
def test_release_webhook_verifies_hmac_and_is_idempotent(
    client: TestClient, db_url: str, tenant_slug: tuple[str, uuid.UUID]
) -> None:
    event = {"kind": "deploy", "service": "shop-api", "version": "v2",
             "occurred_at": T0.isoformat(), "idempotency_key": "ci-run-77"}  # fmt: skip
    body = json.dumps(event).encode()

    def signed(timestamp: int, payload: bytes = body) -> dict[str, str]:
        digest = hmac.new(WEBHOOK.encode(), f"{timestamp}.".encode() + payload,
                          hashlib.sha256).hexdigest()  # fmt: skip
        return {"X-Sentinel-Timestamp": str(timestamp), "X-Sentinel-Signature": f"sha256={digest}"}

    now = int(time.time())
    assert client.post("/v1/events/releases", content=body).status_code == 401
    tampered = body.replace(b"v2", b"v9")
    assert (
        client.post("/v1/events/releases", content=tampered, headers=signed(now)).status_code == 401
    )
    assert (
        client.post("/v1/events/releases", content=body, headers=signed(now - 900)).status_code
        == 401
    )
    assert client.post("/v1/events/releases", content=body, headers=signed(now)).json() == {
        "recorded": True
    }
    assert client.post("/v1/events/releases", content=body, headers=signed(now)).json() == {
        "recorded": False
    }
    assert count(db_url, tenant_slug[1], "SELECT count(*) FROM release_events") == 1


def test_cli_ingest_detect_and_inspect(
    db_url: str, tenant_slug: tuple[str, uuid.UUID], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, tenant = tenant_slug
    monkeypatch.setenv("SENTINELAI_DATABASE_URL", db_url)
    monkeypatch.setenv("SENTINELAI_TENANT", slug)
    onset = datetime(2026, 11, 4, 11, 0, tzinfo=UTC)
    log = tmp_path / "shop-api.jsonl"
    story = traffic(onset - timedelta(minutes=30), 1790, 1, None) + traffic(onset, 90, 4, 4)
    log.write_text("\n".join(story) + "\n", encoding="utf-8")
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"kind": "deploy", "service": "shop-api", "version": "v1.1.0",
                                  "occurred_at": (onset - timedelta(minutes=3)).isoformat(),
                                  "idempotency_key": "d1"}) + "\n", encoding="utf-8")  # fmt: skip

    first = runner.invoke(
        cli, ["ingest", "file", str(log), "--target-config", "demo/app/sentinel.yaml"]
    )
    assert first.exit_code == 0, first.output
    again = runner.invoke(cli, ["ingest", "file", str(log)])  # resumes from the stored cursor
    assert "accepted 0" in again.output
    with log.open("a", encoding="utf-8") as handle:
        handle.write(access(onset + timedelta(seconds=95), "/orders", 201) + "\n")
    assert "accepted 1" in runner.invoke(cli, ["ingest", "file", str(log)]).output
    assert runner.invoke(cli, ["ingest", "events", str(events)]).exit_code == 0

    tick = runner.invoke(cli, ["detect", "run", "--at", (onset + timedelta(seconds=60)).isoformat(),
                               "--target-config", "demo/app/sentinel.yaml", "--json"])  # fmt: skip
    assert tick.exit_code == 0, tick.output
    opened = json.loads(tick.stdout)["opened"]
    assert len(opened) == 1

    listing = json.loads(runner.invoke(cli, ["incidents", "list", "--json"]).stdout)
    assert listing[0]["id"] == opened[0]
    assert listing[0]["class_prior"] == "regression_from_release"
    detail = json.loads(runner.invoke(cli, ["incidents", "show", opened[0], "--json"]).stdout)
    assert detail["suspected_release"]["version"] == "v1.1.0"
    assert detail["fingerprints"][0]["exception_type"] == "AttributeError"
    assert any("after deploy v1.1.0" in r for r in detail["classification_prior"]["reasons"])
    human = runner.invoke(cli, ["incidents", "show", opened[0]])
    assert "regression_from_release" in human.output
    assert count(db_url, tenant, "SELECT count(*) FROM incidents") == 1
