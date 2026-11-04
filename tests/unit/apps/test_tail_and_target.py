from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from sentinelai.apps.ingest_api import verify_release_signature
from sentinelai.apps.tail import read_new_lines
from sentinelai.config.target import load_target_config
from sentinelai.services.ingest import flatten_otlp_logs, parse_change


def test_tail_resumes_and_leaves_partial_lines(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_bytes(b'{"a": 1}\n{"a": 2}\n{"a": 3')  # third line still being written
    first = read_new_lines(log, {})
    assert first.lines == ['{"a": 1}\n', '{"a": 2}\n']
    with log.open("ab") as handle:
        handle.write(b'}\n{"a": 4}\n')
    second = read_new_lines(log, first.cursor)
    assert second.lines == ['{"a": 3}\n', '{"a": 4}\n']
    assert read_new_lines(log, second.cursor).lines == []


def test_tail_detects_truncation_and_copy_truncate_rotation(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_bytes(b'{"old": 1}\n{"old": 2}\n')
    cursor = read_new_lines(log, {}).cursor
    log.write_bytes(b'{"new": 1}\n')  # truncated and rewritten (copy-truncate)
    chunk = read_new_lines(log, cursor)
    assert chunk.rotated
    assert chunk.lines == ['{"new": 1}\n']


def test_tail_batches_at_most_max_lines(tmp_path: Path) -> None:
    log = tmp_path / "big.log"
    log.write_text("".join(f'{{"n": {n}}}\n' for n in range(25)), encoding="utf-8")
    chunk = read_new_lines(log, {}, max_lines=10)
    assert len(chunk.lines) == 10
    assert len(read_new_lines(log, chunk.cursor, max_lines=100).lines) == 15


def test_target_config_for_the_demo_project() -> None:
    target = load_target_config(Path("demo/app/sentinel.yaml"))
    defaults = target.defaults()
    assert defaults.service == "shop-api"
    assert defaults.source_roots == ("shop",)
    assert target.route_tiers()["/orders"] == 0


def test_target_config_custom_fields_and_validation(tmp_path: Path) -> None:
    config = tmp_path / "sentinel.yaml"
    config.write_text(
        "service: billing\nsource_roots: [src/billing/]\n"
        "telemetry:\n  app_root: /srv/app\n  fields:\n    message: [log_line]\n", encoding="utf-8"
    )  # fmt: skip
    target = load_target_config(config)
    assert target.field_mapping().message[0] == "log_line"
    assert target.defaults().app_roots == ("/srv/app",)
    config.write_text("telemetry:\n  fields:\n    nonsense: [x]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"unknown telemetry\.fields"):
        load_target_config(config).field_mapping()


def test_otlp_json_is_flattened_with_resource_attributes() -> None:
    payload = {"resourceLogs": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]},
        "scopeLogs": [{"scope": {"name": "app.orders"}, "logRecords": [{
            "timeUnixNano": "1793793600000000000", "severityNumber": 17,
            "body": {"stringValue": "boom"}, "traceId": "ab" * 16,
            "attributes": [{"key": "exception.type", "value": {"stringValue": "KeyError"}},
                           {"key": "http.status_code", "value": {"intValue": "500"}}],
        }]}],
    }]}  # fmt: skip
    (record,) = list(flatten_otlp_logs(payload))
    assert record["service.name"] == "checkout"
    assert (record["level"], record["message"], record["logger"]) == ("error", "boom", "app.orders")
    assert record["http.status_code"] == 500
    assert record["timestamp"] == 1793793600000000000


def test_change_events_reject_bad_input() -> None:
    ok = parse_change({"kind": "config_change", "service": "s", "version": "v1",
                       "occurred_at": "2026-11-04T10:00:00", "idempotency_key": "k",
                       "changed_keys": ["DB_URL"]})  # fmt: skip
    assert ok.occurred_at.tzinfo is not None
    assert ok.changed_keys == ("DB_URL",)
    with pytest.raises(ValueError, match="invalid change event"):
        parse_change({"kind": "deploy", "service": "s", "version": "v", "occurred_at": "x",
                      "idempotency_key": "k"})  # fmt: skip


def test_release_signature_rules() -> None:
    secret = SecretStr("whsec-test-123456")
    body = b'{"kind":"deploy"}'
    now = int(time.time())
    good = "sha256=" + hmac.new(b"whsec-test-123456", f"{now}.".encode() + body,
                                hashlib.sha256).hexdigest()  # fmt: skip
    verify_release_signature(body, str(now), good, secret, now=now)
    for timestamp, signature, expected in [
        (str(now), "sha256=" + "0" * 64, 401),
        (str(now - 600), good, 401),  # stale: replay window exceeded
        (None, good, 401),
        ("abc", good, 401),
    ]:
        with pytest.raises(HTTPException) as caught:
            verify_release_signature(body, timestamp, signature, secret, now=now)
        assert caught.value.status_code == expected
    with pytest.raises(HTTPException) as unconfigured:
        verify_release_signature(body, str(now), good, None, now=now)
    assert unconfigured.value.status_code == 503  # fails closed
