"""AT-DB-01: migrations apply up -> down -> up on an empty database, atomically."""

from __future__ import annotations

import psycopg
import pytest

from sentinelai.adapters.db import migrate
from tests.db.conftest import temporary_database

APP_TABLES = {
    "tenants", "users", "tenant_memberships", "repositories", "services", "repo_revisions",
    "release_events", "audit_log", "jobs", "outbox_events",
}  # fmt: skip


def _public_objects(url: str) -> dict[str, set[str]]:
    with psycopg.connect(url) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        enums = {
            r[0]
            for r in conn.execute(
                "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = 'public' AND t.typtype = 'e'"
            )
        }
        functions = {
            r[0]
            for r in conn.execute(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "LEFT JOIN pg_depend d ON d.objid = p.oid AND d.deptype = 'e' "
                "WHERE n.nspname = 'public' AND d.objid IS NULL"
            )
        }
        extensions = {
            r[0]
            for r in conn.execute(
                "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pgcrypto')"
            )
        }
    return {"tables": tables, "enums": enums, "functions": functions, "extensions": extensions}


@pytest.mark.at("AT-DB-01")
def test_upgrade_downgrade_upgrade(admin_base_url: str) -> None:
    with temporary_database(admin_base_url, migrated=False) as url:
        migrate.upgrade(url)
        assert migrate.current_revision(url) == "0001"
        after_up = _public_objects(url)
        assert after_up["tables"] >= APP_TABLES
        assert len(after_up["enums"]) == 17
        assert {"current_tenant", "audit_append", "audit_append_platform"} <= after_up["functions"]
        assert after_up["extensions"] == {"vector", "pgcrypto"}

        migrate.downgrade(url, "base")
        assert migrate.current_revision(url) is None
        after_down = _public_objects(url)
        assert after_down == {
            "tables": {"alembic_version"},
            "enums": set(),
            "functions": set(),
            "extensions": set(),
        }

        migrate.upgrade(url)
        assert migrate.current_revision(url) == "0001"
        assert _public_objects(url) == after_up


@pytest.mark.at("AT-DB-01")
def test_failed_migration_leaves_no_partial_schema(admin_base_url: str) -> None:
    """A conflict half-way through rolls back everything, including the version stamp."""
    with temporary_database(admin_base_url, migrated=False) as url:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CREATE TABLE jobs (id int)")  # collides with a later CREATE TABLE
        with pytest.raises(Exception, match="already exists"):
            migrate.upgrade(url)
        objects = _public_objects(url)
        assert objects["tables"] == {"jobs"}  # only the pre-existing table
        assert objects["enums"] == set()
        assert objects["functions"] == set()
        with psycopg.connect(url) as conn:
            version_table = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()
        assert version_table is not None
        assert version_table[0] is None
