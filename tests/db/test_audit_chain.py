"""AT-DB-03 and AT-AUD-01..03: insert-only tables and audit-chain integrity."""

from __future__ import annotations

import threading
import uuid

import psycopg
import pytest
from psycopg import errors, sql

from sentinelai.adapters.db.session import tenant_transaction, verify_chain
from sentinelai.domain.enums import PLATFORM_CHAIN_ID
from tests.db.conftest import SeededTenant, scoped

APPEND = "SELECT audit_append('service', 'test', %s, 'entity', %s, %s::jsonb)"


def append(db_url: str, tenant: uuid.UUID, count: int, *, role: str = "r_api") -> list[int]:
    ids = []
    with psycopg.connect(db_url) as conn:
        for index in range(count):
            with tenant_transaction(conn, tenant, role=role):
                row = conn.execute(
                    APPEND, ("test.action", str(index), f'{{"n": {index}, "note": "ü ✓"}}')
                ).fetchone()
                assert row is not None
                ids.append(row[0])
    return ids


# --------------------------------------------------------------------------- AT-DB-03


@pytest.mark.at("AT-DB-03")
def test_insert_only_tables_reject_mutation_even_for_superuser(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a = two_tenants[0]
    with scoped(admin, "r_api", a.id):
        admin.execute(APPEND, ("x", "1", "{}"))
    statements = {
        "audit_log": "UPDATE audit_log SET action = 'forged'",
        "repo_revisions": "UPDATE repo_revisions SET author = 'forged'",
        "release_events": "UPDATE release_events SET version = 'forged'",
    }
    for table, update in statements.items():
        for statement in (update, f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):  # noqa: S608
            with (
                pytest.raises(errors.InsufficientPrivilege, match="insert-only"),
                admin.transaction(),
            ):
                admin.execute(statement)


@pytest.mark.at("AT-DB-03")
def test_application_roles_cannot_touch_audit_rows_directly(
    admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a = two_tenants[0]
    for role in ("r_api", "r_orch", "r_telemetry", "r_broker", "r_deployer", "r_maint"):
        for statement in (
            "INSERT INTO audit_log (chain_id, ts, actor_type, actor_id, action, entity_type, "
            "entity_id, prev_hash, row_hash) VALUES (gen_random_uuid(), now(), 'user', 'x', 'x', "
            "'x', 'x', '\\x00', '\\x00')",
            "UPDATE audit_log SET action = 'x'",
            "DELETE FROM audit_log",
        ):
            with (
                pytest.raises(errors.InsufficientPrivilege, match="permission denied"),
                scoped(admin, role, a.id),
            ):
                admin.execute(statement)


# --------------------------------------------------------------------------- AT-AUD-01


@pytest.mark.at("AT-AUD-01")
def test_chain_verifies_and_reports_the_first_tampered_row(
    db_url: str, committed_tenant: uuid.UUID
) -> None:
    ids = append(db_url, committed_tenant, 5)
    with psycopg.connect(db_url) as admin:
        report = verify_chain(admin, committed_tenant)
        assert report.ok
        assert report.rows == 5

        tampering = {
            "UPDATE audit_log SET payload = '{\"n\": 99}' WHERE id = %s": (ids[2], "hash_mismatch"),
            "UPDATE audit_log SET ts = ts + interval '1 microsecond' WHERE id = %s": (
                ids[2],
                "hash_mismatch",
            ),
            "UPDATE audit_log SET actor_id = 'someone-else' WHERE id = %s": (
                ids[2],
                "hash_mismatch",
            ),
            "DELETE FROM audit_log WHERE id = %s": (ids[1], "prev_mismatch"),
        }
        for statement, (target, reason) in tampering.items():
            admin.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable")
            admin.execute(statement, (target,))
            report = verify_chain(admin, committed_tenant)
            admin.rollback()
            assert report.first_break is not None, statement
            expected_row = ids[2] if reason == "prev_mismatch" else target
            assert report.first_break.row_id == expected_row, statement
            assert report.first_break.reason == reason, statement

        assert verify_chain(admin, committed_tenant).ok  # all tampering rolled back


@pytest.mark.at("AT-AUD-01")
def test_audit_append_access_rules(
    db_url: str, admin: psycopg.Connection, two_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = two_tenants
    with (
        pytest.raises(errors.InsufficientPrivilege, match="tenant context not set"),
        scoped(admin, "r_api", None),
    ):
        admin.execute(APPEND, ("x", "1", "{}"))
    with (
        pytest.raises(errors.InsufficientPrivilege, match="platform chain"),
        scoped(admin, "r_api", PLATFORM_CHAIN_ID),
    ):
        admin.execute(APPEND, ("x", "1", "{}"))
    with (
        pytest.raises(errors.InsufficientPrivilege, match="unknown tenant"),
        scoped(admin, "r_api", uuid.uuid4()),
    ):
        admin.execute(APPEND, ("x", "1", "{}"))
    with (
        pytest.raises(errors.InsufficientPrivilege, match="permission denied"),
        scoped(admin, "r_orch", a.id),
    ):
        admin.execute("SELECT audit_append_platform('service', 'x', 'x', 'x', 'x', '{}')")
    with (
        pytest.raises(errors.InsufficientPrivilege, match="permission denied"),
        scoped(admin, "r_api", a.id),
    ):
        admin.execute("SELECT audit_write_chain(%s, 'service', 'x', 'x', 'x', 'x', '{}')", (b.id,))
    with scoped(admin, "r_api", None):
        admin.execute(
            "SELECT audit_append_platform('user', 'admin', 'tenant.created', 't', 'x', '{}')"
        )

    # readers see only their own tenant's chain
    with scoped(admin, "r_api", a.id):
        admin.execute(APPEND, ("a.only", "1", "{}"))
    with scoped(admin, "r_api", b.id):
        admin.execute(APPEND, ("b.only", "1", "{}"))
    with scoped(admin, "r_readonly", a.id):
        chains = {r[0] for r in admin.execute("SELECT chain_id FROM audit_log")}
    assert chains == {a.id}
    with (
        pytest.raises(errors.InsufficientPrivilege, match="permission denied"),
        scoped(admin, "r_api", a.id),
    ):
        admin.execute("SELECT 1 FROM audit_log")


@pytest.mark.at("AT-AUD-01")
def test_append_refused_outside_read_committed(db_url: str, committed_tenant: uuid.UUID) -> None:
    with psycopg.connect(db_url) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with (
            pytest.raises(errors.ActiveSqlTransaction, match="READ COMMITTED"),
            tenant_transaction(conn, committed_tenant, role="r_api"),
        ):
            conn.execute(APPEND, ("x", "1", "{}"))


# --------------------------------------------------------------------------- AT-AUD-02


@pytest.mark.at("AT-AUD-02")
def test_chain_is_independent_of_session_timezone(db_url: str, committed_tenant: uuid.UUID) -> None:
    zones = ("UTC", "Asia/Kolkata", "America/Los_Angeles")
    with psycopg.connect(db_url) as writer:
        for zone in zones:
            writer.execute(sql.SQL("SET TIME ZONE {}").format(sql.Literal(zone)))
            writer.commit()
            with tenant_transaction(writer, committed_tenant, role="r_api"):
                writer.execute(APPEND, (f"written.in.{zone}", zone, "{}"))
    for zone in zones:
        with psycopg.connect(db_url) as verifier:
            verifier.execute(sql.SQL("SET TIME ZONE {}").format(sql.Literal(zone)))
            report = verify_chain(verifier, committed_tenant)
            assert report.ok, zone
            assert report.rows == 3


# --------------------------------------------------------------------------- AT-AUD-03


@pytest.mark.at("AT-AUD-03")
def test_concurrent_appends_keep_every_chain_linear(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        tenants = [
            conn.execute(
                "INSERT INTO tenants (slug, name) VALUES (%s, 'x') RETURNING id",
                (f"conc-{uuid.uuid4().hex[:8]}",),
            ).fetchone()[0]  # type: ignore[index]
            for _ in range(2)
        ]
    workers, per_worker = 16, 25
    errors_seen: list[BaseException] = []
    start = threading.Barrier(workers)

    def work(index: int) -> None:
        try:
            with psycopg.connect(db_url) as conn:
                start.wait()
                for n in range(per_worker):
                    with tenant_transaction(conn, tenants[index % 2], role="r_telemetry"):
                        conn.execute(APPEND, ("concurrent", f"{index}-{n}", "{}"))
        except BaseException as exc:  # surfaced below
            errors_seen.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors_seen == []

    with psycopg.connect(db_url) as admin:
        for tenant in tenants:
            report = verify_chain(admin, tenant)
            assert report.ok, report.first_break
            assert report.rows == workers * per_worker // 2
