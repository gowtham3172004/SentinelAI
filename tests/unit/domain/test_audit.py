from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinelai.domain.audit import (
    GENESIS_PREV_HASH,
    AuditRow,
    epoch_microseconds,
    row_hash,
    verify_rows,
)

CHAIN = UUID("11111111-2222-3333-4444-555555555555")


def make_chain(count: int) -> list[AuditRow]:
    rows: list[AuditRow] = []
    prev = GENESIS_PREV_HASH
    for index in range(count):
        draft = AuditRow(
            id=index + 1,
            chain_id=CHAIN,
            ts=datetime(2026, 11, 3, 12, 0, tzinfo=UTC) + timedelta(microseconds=index),
            actor_type="service",
            actor_id="orchestrator",
            action="run.state_changed",
            entity_type="run",
            entity_id=f"RUN-{index}",
            payload_text='{"to": "triage"}',
            prev_hash=prev,
            row_hash=b"",
        )
        row = replace(draft, row_hash=row_hash(prev, draft))
        rows.append(row)
        prev = row.row_hash
    return rows


def test_hash_matches_the_sql_formula() -> None:
    row = make_chain(1)[0]
    material = (
        f"{CHAIN}|{epoch_microseconds(row.ts)}|service|orchestrator|run.state_changed|run|RUN-0|"
        '{"to": "triage"}'
    )
    assert row.row_hash == hashlib.sha256(b"\x00" + material.encode()).digest()


def test_epoch_microseconds_ignores_the_display_timezone() -> None:
    instant = datetime(2026, 11, 3, 12, 0, 0, 123456, tzinfo=UTC)
    kolkata = instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert epoch_microseconds(instant) == epoch_microseconds(kolkata)
    with pytest.raises(ValueError, match="timezone-aware"):
        epoch_microseconds(datetime(2026, 1, 1))


def test_intact_chain_verifies() -> None:
    report = verify_rows(CHAIN, make_chain(5))
    assert report.ok
    assert report.rows == 5
    assert report.head_hash is not None


def test_empty_chain_is_ok() -> None:
    report = verify_rows(CHAIN, [])
    assert report.ok
    assert report.head_hash is None


@given(index=st.integers(0, 5), field=st.sampled_from(["actor_id", "action", "payload_text"]))
def test_any_altered_field_is_detected_at_that_row(index: int, field: str) -> None:
    rows = make_chain(6)
    changes: dict[str, Any] = {field: "tampered"}
    rows[index] = replace(rows[index], **changes)
    report = verify_rows(CHAIN, rows)
    assert report.first_break is not None
    assert (report.first_break.row_id, report.first_break.reason) == (index + 1, "hash_mismatch")


@given(index=st.integers(0, 4))
def test_a_removed_row_is_detected_at_the_next_row(index: int) -> None:
    rows = make_chain(6)
    del rows[index]
    report = verify_rows(CHAIN, rows)
    assert report.first_break is not None
    assert report.first_break.reason == "prev_mismatch"
    assert report.first_break.row_id == index + 2
