"""CLI over a real database: `db current`, `db downgrade` guard, `audit verify` (P0-08)."""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from typer.testing import CliRunner

from sentinelai.adapters.db.session import tenant_transaction
from sentinelai.cli.main import app

runner = CliRunner()


@pytest.fixture
def admin_env(db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SENTINELAI_ADMIN_DATABASE_URL", db_url)
    return db_url


def _password(url: str) -> str:
    password = psycopg.conninfo.conninfo_to_dict(url).get("password")
    assert password
    return str(password)


def test_db_current(admin_env: str) -> None:
    result = runner.invoke(app, ["db", "current"])
    assert result.exit_code == 0, result.output
    assert "0002" in result.stdout
    assert _password(admin_env) not in result.output


def test_downgrade_requires_confirmation(admin_env: str) -> None:
    result = runner.invoke(app, ["db", "downgrade", "--revision", "base"])
    assert result.exit_code == 2
    assert "--yes" in result.output


def test_grants_sql_rejects_unknown_tables() -> None:
    assert runner.invoke(app, ["db", "grants-sql", "--table", "users"]).exit_code == 0
    assert runner.invoke(app, ["db", "grants-sql", "--table", "nope"]).exit_code == 2


def test_missing_admin_url_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTINELAI_ADMIN_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["audit", "verify"])
    assert result.exit_code == 2
    assert "SENTINELAI_ADMIN_DATABASE_URL" in result.output


def test_connection_errors_never_print_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "pw-" + uuid.uuid4().hex
    monkeypatch.setenv(
        "SENTINELAI_ADMIN_DATABASE_URL",
        f"postgresql://postgres:{secret}@127.0.0.1:9/nope?connect_timeout=2",
    )
    for args in (["audit", "verify"], ["db", "current"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, result.output
        assert "database error" in result.output
        assert secret not in result.output


@pytest.mark.at("AT-AUD-01")
def test_audit_verify_detects_a_committed_break(
    admin_env: str, committed_tenant: uuid.UUID
) -> None:
    with psycopg.connect(admin_env) as conn:
        for n in range(3):
            with tenant_transaction(conn, committed_tenant, role="r_api"):
                conn.execute("SELECT audit_append('user', 'u1', 'x', 'e', %s, '{}')", (str(n),))
    ok = runner.invoke(app, ["audit", "verify", "--chain", str(committed_tenant), "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)[0]["rows"] == 3

    with psycopg.connect(admin_env, autocommit=True) as conn, conn.transaction():
        conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable")
        conn.execute(
            "UPDATE audit_log SET actor_id = 'forged' WHERE id = "
            "(SELECT max(id) FROM audit_log WHERE chain_id = %s)",
            (committed_tenant,),
        )
        conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable")

    broken = runner.invoke(app, ["audit", "verify", "--chain", str(committed_tenant), "--json"])
    assert broken.exit_code == 1
    report = json.loads(broken.stdout)[0]
    assert report["ok"] is False
    assert report["first_break"]["reason"] == "hash_mismatch"
