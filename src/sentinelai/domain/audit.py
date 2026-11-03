"""Audit hash chain: the pure hash function shared by the database writer and the verifier.

``row_hash`` must reproduce, byte for byte, what ``audit_write_chain()`` computes in SQL:

    sha256(prev_hash || utf8(concat_ws('|', chain_id, epoch_microseconds(ts), actor_type,
                                       actor_id, action, entity_type, entity_id, payload::text)))

The timestamp enters as integer epoch microseconds, so the hash never depends on a session's
TimeZone or DateStyle setting (finding C4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

GENESIS_PREV_HASH = b"\x00"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def epoch_microseconds(ts: datetime) -> int:
    if ts.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return (ts - _EPOCH) // timedelta(microseconds=1)


@dataclass(frozen=True)
class AuditRow:
    id: int
    chain_id: UUID
    ts: datetime
    actor_type: str
    actor_id: str
    action: str
    entity_type: str
    entity_id: str
    payload_text: str  # the database's canonical jsonb text, fetched as payload::text
    prev_hash: bytes
    row_hash: bytes


def row_hash(prev_hash: bytes, row: AuditRow) -> bytes:
    material = "|".join(
        (
            str(row.chain_id),
            str(epoch_microseconds(row.ts)),
            row.actor_type,
            row.actor_id,
            row.action,
            row.entity_type,
            row.entity_id,
            row.payload_text,
        )
    )
    return hashlib.sha256(prev_hash + material.encode("utf-8")).digest()


@dataclass(frozen=True)
class ChainBreak:
    row_id: int
    reason: str  # prev_mismatch (row removed or reordered) | hash_mismatch (row altered)


@dataclass(frozen=True)
class ChainReport:
    chain_id: UUID
    rows: int
    first_break: ChainBreak | None
    head_hash: str | None

    @property
    def ok(self) -> bool:
        return self.first_break is None


def verify_rows(chain_id: UUID, rows: list[AuditRow]) -> ChainReport:
    """Verify rows of one chain, ordered by id. Reports the first broken row, if any."""
    expected_prev = GENESIS_PREV_HASH
    for row in rows:
        if row.prev_hash != expected_prev:
            return ChainReport(chain_id, len(rows), ChainBreak(row.id, "prev_mismatch"), None)
        if row_hash(row.prev_hash, row) != row.row_hash:
            return ChainReport(chain_id, len(rows), ChainBreak(row.id, "hash_mismatch"), None)
        expected_prev = row.row_hash
    head = rows[-1].row_hash.hex() if rows else None
    return ChainReport(chain_id, len(rows), None, head)
