"""AT-TEN-01..03: row-level tenant isolation, fail-closed context, cross-tenant references."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from psycopg import errors

from sentinelai.adapters.db.authority import load_matrix
from sentinelai.adapters.db.session import (
    NestedTransactionError,
    system_transaction,
    tenant_transaction,
)
from tests.db.conftest import ZERO_BUCKETS, SeededTenant, scoped

MATRIX = load_matrix()
TENANT_TABLES = MATRIX.tables_with_scope("tenant")


def readers(table: str) -> list[str]:
    return [role for role in MATRIX.roles if MATRIX.allows(role, table, "select")]


@dataclass(frozen=True)
class WriteCase:
    role: str
    insert: Callable[[SeededTenant, SeededTenant], tuple[str, tuple[object, ...]]]
    update: tuple[str, str] | None = None  # (statement for tenant %s, column showing tampering)
    has_parent: bool = True  # whether the row references a parent row (cross-tenant FK test)


def _tok() -> str:
    return secrets.token_hex(4)


# insert(row_owner, parent_owner): a row claiming row_owner's tenant, pointing at parent_owner's
# parent rows. Same tenant twice is a legitimate insert.
WRITES: dict[str, WriteCase] = {
    "repositories": WriteCase(
        "r_api",
        lambda o, _p: ("INSERT INTO repositories (tenant_id, name, url) VALUES (%s, %s, 'git@x:y')",
                       (o.id, "repo-" + _tok())),
        ("UPDATE repositories SET default_branch = 'tampered' WHERE tenant_id = %s",
         "default_branch"),
        has_parent=False,
    ),
    "services": WriteCase(
        "r_api",
        lambda o, p: ("INSERT INTO services (tenant_id, name, environment, repository_id) "
                      "VALUES (%s, %s, 'prod', %s)", (o.id, "svc-" + _tok(), p.repository)),
        ("UPDATE services SET app_root = '/tampered' WHERE tenant_id = %s", "app_root"),
    ),
    "repo_revisions": WriteCase(
        "r_codeintel",
        lambda o, p: ("INSERT INTO repo_revisions (tenant_id, repository_id, sha) "
                      "VALUES (%s, %s, %s)", (o.id, p.repository, secrets.token_hex(20))),
    ),
    "release_events": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO release_events (tenant_id, service_id, kind, version, "
                      "idempotency_key, occurred_at) VALUES (%s, %s, 'deploy', 'v2', %s, now())",
                      (o.id, p.service, _tok())),
    ),
    "ingest_sources": WriteCase(
        "r_telemetry",
        lambda o, _p: ("INSERT INTO ingest_sources (tenant_id, name, kind) "
                       "VALUES (%s, %s, 'http')", (o.id, "src-" + _tok())),
        ("UPDATE ingest_sources SET cursor = '{\"tampered\": 1}' WHERE tenant_id = %s", "cursor"),
        has_parent=False,
    ),
    "ingest_batches": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO ingest_batches (tenant_id, source_id, batch_id, records) "
                      "VALUES (%s, %s, %s, 1)", (o.id, p.source, _tok())),
    ),
    "dead_letters": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO dead_letters (tenant_id, source_id, reason, raw) "
                      "VALUES (%s, %s, 'r', 'x')", (o.id, p.source)),
    ),
    "log_events": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO log_events (tenant_id, service_id, ts, level, message) "
                      "VALUES (%s, %s, now(), 'info', 'm')", (o.id, p.service)),
    ),
    "request_windows": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO request_windows (tenant_id, service_id, route, window_start, "
                      "requests, errors_5xx, errors_4xx, latency) VALUES (%s, %s, %s, now(), "
                      "1, 0, 0, %s)", (o.id, p.service, "/r-" + _tok(), ZERO_BUCKETS)),
        ("UPDATE request_windows SET route = 'tampered' WHERE tenant_id = %s", "route"),
    ),
    "exception_windows": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO exception_windows (tenant_id, service_id, fingerprint, "
                      "window_start, occurrences) VALUES (%s, %s, %s, now(), 1)",
                      (o.id, p.service, _tok())),
        ("UPDATE exception_windows SET fingerprint = 'tampered' WHERE tenant_id = %s",
         "fingerprint"),
    ),
    "fingerprints": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO fingerprints (tenant_id, service_id, fingerprint, "
                      "exception_type, sample_message, top_frames, library_only, first_seen, "
                      "last_seen, occurrences) VALUES (%s, %s, %s, 'E', 'm', '[]', false, now(), "
                      "now(), 1)", (o.id, p.service, _tok())),
        ("UPDATE fingerprints SET sample_message = 'tampered' WHERE tenant_id = %s",
         "sample_message"),
    ),
    "incidents": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO incidents (tenant_id, service_id, severity, title, first_seen, "
                      "last_signal_at) VALUES (%s, %s, 'sev3', 't', now(), now())",
                      (o.id, p.service)),
        ("UPDATE incidents SET title = 'tampered' WHERE tenant_id = %s", "title"),
    ),
    "incident_signals": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO incident_signals (tenant_id, incident_id, service_id, kind, "
                      "key, fired_at, window_start, window_end) VALUES (%s, %s, %s, 'LATENCY', "
                      "%s, now(), now(), now())", (o.id, p.incident, p.service, _tok())),
    ),
    "incident_fingerprints": WriteCase(
        "r_telemetry",
        lambda o, p: ("INSERT INTO incident_fingerprints (tenant_id, incident_id, fingerprint, "
                      "first_seen, last_seen) VALUES (%s, %s, %s, now(), now())",
                      (o.id, p.incident, _tok())),
        ("UPDATE incident_fingerprints SET occurrences = 999 WHERE tenant_id = %s",
         "occurrences"),
    ),
    "noise_rules": WriteCase(
        "r_api",
        lambda o, p: ("INSERT INTO noise_rules (tenant_id, service_id, fingerprint) "
                      "VALUES (%s, %s, %s)", (o.id, p.service, _tok())),
        ("UPDATE noise_rules SET reason = 'tampered' WHERE tenant_id = %s", "reason"),
    ),
}  # fmt: skip


def test_write_cases_cover_every_tenant_table() -> None:
    assert set(WRITES) == set(TENANT_TABLES)
    for table, case in WRITES.items():
        assert MATRIX.allows(case.role, table, "insert")


# --------------------------------------------------------------------------- AT-TEN-01


@pytest.mark.at("AT-TEN-01")
@pytest.mark.parametrize("table", TENANT_TABLES)
def test_reads_see_only_own_tenant(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant], table: str
) -> None:
    for tenant in two_tenants:
        for role in readers(table):
            with scoped(admin, role, tenant.id):
                seen = [r[0] for r in admin.execute(f"SELECT tenant_id FROM {table}")]  # noqa: S608
            assert seen == [tenant.id], f"{role} in {tenant.id} saw {seen} in {table}"


@pytest.mark.at("AT-TEN-01")
@pytest.mark.parametrize("table", TENANT_TABLES)
def test_cannot_insert_rows_for_another_tenant(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant], table: str
) -> None:
    a, b = two_tenants
    case = WRITES[table]
    statement, params = case.insert(b, b)
    with (
        pytest.raises(errors.InsufficientPrivilege, match="row-level security"),
        scoped(admin, case.role, a.id),
    ):
        admin.execute(statement, params)
    statement, params = case.insert(a, a)
    with scoped(admin, case.role, a.id):
        admin.execute(statement, params)  # own tenant: allowed


@pytest.mark.at("AT-TEN-01")
@pytest.mark.parametrize("table", [t for t in TENANT_TABLES if WRITES[t].has_parent])
def test_cannot_reference_another_tenants_rows(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant], table: str
) -> None:
    """Composite (tenant_id, id) foreign keys: FK checks bypass RLS, so this is the guard."""
    a, b = two_tenants
    case = WRITES[table]
    statement, params = case.insert(a, b)  # own tenant id, foreign parent row
    with pytest.raises(errors.ForeignKeyViolation), scoped(admin, case.role, a.id):
        admin.execute(statement, params)


@pytest.mark.at("AT-TEN-01")
@pytest.mark.parametrize("table", [t for t, c in WRITES.items() if c.update])
def test_cannot_update_another_tenants_rows(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant], table: str
) -> None:
    a, b = two_tenants
    case = WRITES[table]
    assert case.update is not None
    statement, column = case.update
    with scoped(admin, case.role, a.id):
        assert admin.execute(statement, (b.id,)).rowcount == 0
        assert admin.execute(statement, (a.id,)).rowcount == 1
    row = admin.execute(f"SELECT {column} FROM {table} WHERE tenant_id = %s", (b.id,)).fetchone()  # noqa: S608
    assert row is not None
    assert "tampered" not in str(row[0])
    assert "999" not in str(row[0])


@pytest.mark.at("AT-TEN-01")
def test_bypassrls_maintenance_role_cannot_read_tenant_data(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    for table in TENANT_TABLES:
        with (
            pytest.raises(errors.InsufficientPrivilege, match="permission denied"),
            scoped(admin, "r_maint", two_tenants[0].id),
        ):
            admin.execute(f"SELECT 1 FROM {table}")  # noqa: S608


# --------------------------------------------------------------------------- AT-TEN-02


@pytest.mark.at("AT-TEN-02")
@pytest.mark.parametrize("table", TENANT_TABLES)
@pytest.mark.parametrize(
    ("context", "error"),
    [(None, errors.InsufficientPrivilege), ("not-a-uuid", errors.InvalidTextRepresentation)],
)
def test_missing_or_malformed_context_fails_closed(
    admin: psycopg.Connection,
    two_tenants: tuple[SeededTenant, SeededTenant],
    table: str,
    context: str | None,
    error: type[Exception],
) -> None:
    for role in readers(table):
        with pytest.raises(error), scoped(admin, role, context):
            admin.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608


@pytest.mark.at("AT-TEN-02")
def test_unknown_tenant_context_sees_nothing(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    for table in TENANT_TABLES:
        with scoped(admin, "r_readonly", uuid.uuid4()):
            row = admin.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        assert row == (0,)


@pytest.mark.at("AT-TEN-02")
def test_context_does_not_outlive_its_transaction(
    db_url: str, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    with psycopg.connect(db_url) as conn:
        with tenant_transaction(conn, uuid.uuid4(), role="r_readonly"):
            pass
        row = conn.execute("SELECT current_setting('sentinel.tenant_id', true)").fetchone()
        assert row is not None
        assert row[0] in ("", None)
        conn.rollback()


def test_scoped_transactions_refuse_to_nest(db_url: str) -> None:
    with psycopg.connect(db_url) as conn:
        conn.execute("SELECT 1")  # opens a transaction implicitly
        with pytest.raises(NestedTransactionError), tenant_transaction(conn, uuid.uuid4()):
            pass
        with pytest.raises(NestedTransactionError), system_transaction(conn):
            pass
        conn.rollback()


# --------------------------------------------------------------------------- AT-TEN-03


@pytest.mark.at("AT-TEN-03")
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    table=st.sampled_from(TENANT_TABLES),
    context_index=st.integers(0, 1),
    target_index=st.integers(0, 1),
    operation=st.sampled_from(["select", "insert", "update"]),
    data=st.data(),
)
def test_random_operations_never_cross_tenants(
    admin: psycopg.Connection,
    two_tenants: tuple[SeededTenant, SeededTenant],
    table: str,
    context_index: int,
    target_index: int,
    operation: str,
    data: st.DataObject,
) -> None:
    context, target = two_tenants[context_index], two_tenants[target_index]
    # Every example starts from the same seeded state: its effects are always rolled back.
    with admin.transaction(force_rollback=True):
        _random_operation(admin, context, target, table, operation, data)


def _random_operation(
    admin: psycopg.Connection,
    context: SeededTenant,
    target: SeededTenant,
    table: str,
    operation: str,
    data: st.DataObject,
) -> None:
    if operation == "select":
        role = data.draw(st.sampled_from(readers(table)))
        with scoped(admin, role, context.id):
            seen = {r[0] for r in admin.execute(f"SELECT tenant_id FROM {table}")}  # noqa: S608
        assert seen == {context.id}
    elif operation == "insert":
        case = WRITES[table]
        statement, params = case.insert(target, target)
        if target is context:
            with scoped(admin, case.role, context.id):
                admin.execute(statement, params)
        else:
            with pytest.raises(errors.InsufficientPrivilege), scoped(admin, case.role, context.id):
                admin.execute(statement, params)
    else:
        case = WRITES[table]
        if case.update is None:
            return
        with scoped(admin, case.role, context.id):
            changed = admin.execute(case.update[0], (target.id,)).rowcount
        assert changed == (1 if target is context else 0)
