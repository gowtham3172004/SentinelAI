"""psycopg connections, tenant-scoped transactions and audit-chain reads (P0-10, P0-08).

Every tenant-scoped transaction sets ``sentinel.tenant_id`` with ``set_config(..., true)``,
which is transaction-local: the context can never leak to the next transaction on a pooled
connection. Row-level security then fails closed if the context is missing.

Important limitation (documented in docs/design/04): the tenant context is a session setting
that any SQL client on a connection can set. RLS here protects against application bugs
(a forgotten WHERE clause, a wrong ID), not against a compromised service process. Isolation
against a compromised process needs per-tenant credentials or silo deployment.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import SecretStr

from sentinelai.adapters.redaction import register_secret
from sentinelai.domain.audit import AuditRow, ChainReport, verify_rows


def connect(url: SecretStr | str, **kwargs: object) -> psycopg.Connection:
    raw = url.get_secret_value() if isinstance(url, SecretStr) else url
    info = psycopg.conninfo.conninfo_to_dict(raw)
    password = info.get("password")
    register_secret(str(password) if password is not None else None)
    return psycopg.connect(raw, **kwargs)  # type: ignore[arg-type]


class NestedTransactionError(RuntimeError):
    """Raised when a scoped transaction is opened inside an already-open transaction.

    ``SET LOCAL`` lasts until the end of the *top-level* transaction, so a nested scope would
    leak its tenant context and role to everything that runs after it.
    """


def _require_idle(conn: psycopg.Connection) -> None:
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise NestedTransactionError(
            "tenant/system transactions must be top-level; commit or roll back first"
        )


@contextmanager
def tenant_transaction(
    conn: psycopg.Connection, tenant_id: UUID, *, role: str | None = None
) -> Iterator[psycopg.Connection]:
    """A top-level transaction bound to one tenant, optionally as a group role."""
    _require_idle(conn)
    with conn.transaction():
        if role is not None:
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        conn.execute("SELECT set_config('sentinel.tenant_id', %s, true)", (str(tenant_id),))
        yield conn


@contextmanager
def system_transaction(
    conn: psycopg.Connection, *, role: str | None = None
) -> Iterator[psycopg.Connection]:
    """A top-level transaction with no tenant context (queue, outbox, platform tables)."""
    _require_idle(conn)
    with conn.transaction():
        if role is not None:
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        conn.execute("SELECT set_config('sentinel.tenant_id', '', true)")
        yield conn


_CHAIN_ROWS = """
SELECT id, chain_id, ts, actor_type, actor_id, action, entity_type, entity_id,
       payload::text, prev_hash, row_hash
  FROM audit_log WHERE chain_id = %s ORDER BY id
"""


def audit_chain_ids(conn: psycopg.Connection) -> list[UUID]:
    return [row[0] for row in conn.execute("SELECT DISTINCT chain_id FROM audit_log ORDER BY 1")]


def verify_chain(conn: psycopg.Connection, chain_id: UUID) -> ChainReport:
    rows = [
        AuditRow(
            id=r[0],
            chain_id=r[1],
            ts=r[2],
            actor_type=r[3],
            actor_id=r[4],
            action=r[5],
            entity_type=r[6],
            entity_id=r[7],
            payload_text=r[8],
            prev_hash=bytes(r[9]),
            row_hash=bytes(r[10]),
        )
        for r in conn.execute(_CHAIN_ROWS, (chain_id,))
    ]
    return verify_rows(chain_id, rows)
