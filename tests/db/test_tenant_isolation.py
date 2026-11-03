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
from tests.db.conftest import SeededTenant, scoped

MATRIX = load_matrix()
TENANT_TABLES = MATRIX.tables_with_scope("tenant")


def readers(table: str) -> list[str]:
    return [role for role in MATRIX.roles if MATRIX.allows(role, table, "select")]


@dataclass(frozen=True)
class WriteCase:
    role: str
    insert: Callable[[SeededTenant, SeededTenant], tuple[str, tuple[object, ...]]]
    update: str | None  # statement updating rows of the tenant given as parameter


# insert(row_owner, parent_owner): a row claiming row_owner's tenant, pointing at parent_owner's
# parent rows. Same tenant twice is a legitimate insert.
WRITES: dict[str, WriteCase] = {
    "repositories": WriteCase(
        "r_api",
        lambda o, _p: (
            "INSERT INTO repositories (tenant_id, name, url) VALUES (%s, %s, 'git@x:y')",
            (o.id, "repo-" + secrets.token_hex(4)),
        ),
        "UPDATE repositories SET default_branch = 'tampered' WHERE tenant_id = %s",
    ),
    "services": WriteCase(
        "r_api",
        lambda o, p: (
            "INSERT INTO services (tenant_id, name, environment, repository_id) "
            "VALUES (%s, %s, 'prod', %s)",
            (o.id, "svc-" + secrets.token_hex(4), p.repository),
        ),
        "UPDATE services SET app_root = '/tampered' WHERE tenant_id = %s",
    ),
    "repo_revisions": WriteCase(
        "r_codeintel",
        lambda o, p: (
            "INSERT INTO repo_revisions (tenant_id, repository_id, sha) VALUES (%s, %s, %s)",
            (o.id, p.repository, secrets.token_hex(20)),
        ),
        None,
    ),
    "release_events": WriteCase(
        "r_telemetry",
        lambda o, p: (
            "INSERT INTO release_events (tenant_id, service_id, kind, version, "
            "idempotency_key, occurred_at) VALUES (%s, %s, 'deploy', 'v2', %s, now())",
            (o.id, p.service, secrets.token_hex(8)),
        ),
        None,
    ),
}


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
@pytest.mark.parametrize("table", [t for t in TENANT_TABLES if t != "repositories"])
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
    with scoped(admin, case.role, a.id):
        assert admin.execute(case.update, (b.id,)).rowcount == 0
        assert admin.execute(case.update, (a.id,)).rowcount == 1
    column = "default_branch" if table == "repositories" else "app_root"
    row = admin.execute(f"SELECT {column} FROM {table} WHERE tenant_id = %s", (b.id,)).fetchone()  # noqa: S608
    assert row is not None
    assert "tampered" not in str(row[0])


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
            changed = admin.execute(case.update, (target.id,)).rowcount
        assert changed == (1 if target is context else 0)
