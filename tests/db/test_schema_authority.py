"""AT-DB-02 and schema invariants: the migrated database equals the authority matrix."""

from __future__ import annotations

import psycopg
import pytest

from sentinelai.adapters.db.authority import PRIVILEGES, load_matrix
from sentinelai.domain.enums import PG_ENUMS

MATRIX = load_matrix()
SECURITY_DEFINERS = {"audit_append", "audit_append_platform"}


@pytest.mark.at("AT-DB-02")
def test_every_matrix_cell_matches_the_database(admin: psycopg.Connection) -> None:
    mismatches = []
    for table in MATRIX.tables:
        for role in MATRIX.roles:
            for privilege in PRIVILEGES:
                row = admin.execute(
                    "SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", privilege)
                ).fetchone()
                assert row is not None
                expected = MATRIX.allows(role, table, privilege)
                if row[0] != expected:
                    mismatches.append(f"{role} {privilege} {table}: db={row[0]} matrix={expected}")
    assert mismatches == []


@pytest.mark.at("AT-DB-02")
def test_public_has_no_privileges(admin: psycopg.Connection) -> None:
    for table in MATRIX.tables:
        for privilege in PRIVILEGES:
            row = admin.execute(
                "SELECT has_table_privilege('public', %s, %s)", (f"public.{table}", privilege)
            ).fetchone()
            assert row == (False,), f"PUBLIC has {privilege} on {table}"
    functions = admin.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "LEFT JOIN pg_depend d ON d.objid = p.oid AND d.deptype = 'e' "
        "WHERE n.nspname = 'public' AND d.objid IS NULL"
    ).fetchall()
    for (signature,) in functions:
        row = admin.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE')", (signature,)
        ).fetchone()
        assert row == (False,), f"PUBLIC can execute {signature}"


@pytest.mark.at("AT-DB-02")
def test_function_and_sequence_privileges(admin: psycopg.Connection) -> None:
    for signature, allowed in MATRIX.functions.items():
        for role in MATRIX.roles:
            row = admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, signature)
            ).fetchone()
            assert row == (role in allowed,), f"{role} EXECUTE {signature}"
    internal = "audit_write_chain(uuid, text, text, text, text, text, jsonb)"
    for role in MATRIX.roles:
        row = admin.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, internal)
        ).fetchone()
        assert row == (False,), f"{role} can call the internal chain writer directly"
    for table, spec in MATRIX.tables.items():
        if spec.sequence is None:
            continue
        for role in MATRIX.roles:
            row = admin.execute(
                "SELECT has_sequence_privilege(%s, %s, 'USAGE')", (role, spec.sequence)
            ).fetchone()
            assert row == (MATRIX.allows(role, table, "insert"),), f"{role} USAGE {spec.sequence}"


def test_matrix_covers_exactly_the_application_tables(admin: psycopg.Connection) -> None:
    tables = {
        r[0] for r in admin.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    }
    assert tables - {"alembic_version"} == set(MATRIX.tables)


def test_rls_mode_matches_scope(admin: psycopg.Connection) -> None:
    for table, spec in MATRIX.tables.items():
        row = admin.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{table}",),
        ).fetchone()
        expected = {
            "platform": (False, False),
            "tenant": (True, True),
            "audit_chain": (True, False),
        }
        assert row == expected[spec.scope], f"{table} ({spec.scope})"
        if spec.scope == "tenant":
            policy = admin.execute(
                "SELECT qual, with_check FROM pg_policies "
                "WHERE tablename = %s AND policyname = 'tenant_isolation'",
                (table,),
            ).fetchone()
            assert policy is not None, f"{table} has no tenant_isolation policy"
            assert "current_tenant()" in policy[0]
            assert "current_tenant()" in policy[1]
            nullable = admin.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'tenant_id'",
                (table,),
            ).fetchone()
            assert nullable == ("NO",)


def test_roles_have_least_privilege_attributes(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
        "FROM pg_roles WHERE rolname = ANY(%s)",
        ([*MATRIX.roles, "sentinel_owner"],),
    ).fetchall()
    assert len(rows) == len(MATRIX.roles) + 1
    for name, can_login, superuser, bypass, create_role, create_db in rows:
        assert not can_login, name
        assert not superuser, name
        assert not create_role, name
        assert not create_db, name
        assert bypass == (name == "r_maint"), name


def test_objects_owned_by_non_superuser_owner(admin: psycopg.Connection) -> None:
    owners = {
        r[0]
        for r in admin.execute(
            "SELECT DISTINCT tableowner FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        )
    }
    assert owners == {"sentinel_owner"}


def test_security_definer_functions_pin_search_path(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "SELECT p.proname, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "LEFT JOIN pg_depend d ON d.objid = p.oid AND d.deptype = 'e' "
        "WHERE n.nspname = 'public' AND d.objid IS NULL"
    ).fetchall()
    definers = {name for name, secdef, _, _ in rows if secdef}
    assert definers == SECURITY_DEFINERS
    for name, _, config, owner in rows:
        assert owner == "sentinel_owner", name
        assert config is not None
        assert any(c.startswith("search_path=") for c in config), f"{name} lacks search_path"


def test_domain_enums_equal_database_enums(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "SELECT t.typname, e.enumlabel FROM pg_type t "
        "JOIN pg_enum e ON e.enumtypid = t.oid "
        "JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' ORDER BY t.typname, e.enumsortorder"
    ).fetchall()
    database: dict[str, list[str]] = {}
    for type_name, label in rows:
        database.setdefault(type_name, []).append(label)
    domain = {name: [m.value for m in enum] for name, enum in PG_ENUMS.items()}
    assert database == domain


def test_bootstrap_tenants_exist(admin: psycopg.Connection) -> None:
    slugs = {r[0] for r in admin.execute("SELECT slug FROM tenants")}
    assert {"default", "bench"} <= slugs
