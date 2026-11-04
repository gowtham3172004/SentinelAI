"""Database test fixtures.

Each test session creates a throwaway database, migrates it to head and drops it at the end.
The server is found through SENTINELAI_TEST_ADMIN_URL (a superuser URL to the ``postgres``
database), or else built from the repository's .env (docker compose dev stack). Without
either, every database test is skipped with a clear reason; CI always provides the URL.

Tests seed data as the superuser (who bypasses RLS) inside a transaction that is rolled back
after each test, then act as a group role through ``scoped``.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from sentinelai.adapters.db import migrate
from sentinelai.config.secrets import parse_env_file

REPO_ROOT = Path(__file__).resolve().parents[2]


def _admin_base_url() -> str | None:
    explicit = os.environ.get("SENTINELAI_TEST_ADMIN_URL")
    if explicit:
        return explicit
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return None
    values = parse_env_file(env_file.read_text(encoding="utf-8"))
    password = values.get("SENTINELAI_DEV_DB_PASSWORD")
    if not password:
        return None
    port = values.get("SENTINELAI_DEV_DB_PORT", "55432")
    return f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"


def _with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    db_dir = Path(__file__).parent
    url = _admin_base_url()
    for item in items:
        if Path(str(item.path)).is_relative_to(db_dir):
            item.add_marker(pytest.mark.db)
            if url is None:
                item.add_marker(
                    pytest.mark.skip(
                        reason="no database: set SENTINELAI_TEST_ADMIN_URL or start the "
                        "compose dev stack with a .env (see .env.example)"
                    )
                )


@pytest.fixture(scope="session")
def admin_base_url() -> str:
    url = _admin_base_url()
    assert url is not None
    return url


@contextmanager
def temporary_database(admin_base_url: str, *, migrated: bool) -> Iterator[str]:
    name = "sentinel_test_" + secrets.token_hex(6)
    with psycopg.connect(admin_base_url, autocommit=True, connect_timeout=10) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = _with_database(admin_base_url, name)
    try:
        if migrated:
            migrate.upgrade(url)
        yield url
    finally:
        with psycopg.connect(admin_base_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="session")
def db_url(admin_base_url: str) -> Iterator[str]:
    with temporary_database(admin_base_url, migrated=True) as url:
        yield url


@pytest.fixture
def admin(db_url: str) -> Iterator[psycopg.Connection]:
    """Superuser connection; everything it does is rolled back after the test."""
    conn = psycopg.connect(db_url)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@contextmanager
def scoped(
    conn: psycopg.Connection, role: str, tenant: UUID | str | None = None
) -> Iterator[psycopg.Connection]:
    """Act as ``role`` (optionally in a tenant context) inside a savepoint, then reset."""
    with conn.transaction():
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        value = "" if tenant is None else str(tenant)
        conn.execute("SELECT set_config('sentinel.tenant_id', %s, true)", (value,))
        yield conn
        conn.execute("RESET ROLE")
        conn.execute("SELECT set_config('sentinel.tenant_id', '', true)")


@dataclass(frozen=True)
class SeededTenant:
    """Exactly one row per tenant-scoped table, so isolation tests can count precisely."""

    id: UUID
    repository: UUID
    service: UUID
    revision: UUID
    release: UUID
    source: UUID
    incident: UUID


ZERO_BUCKETS = "{0,0,0,0,0,0,0,0,0,0,0,0}"


def seed_tenant(conn: psycopg.Connection, slug: str | None = None) -> SeededTenant:
    slug = slug or "t-" + secrets.token_hex(5)

    def one(query: str, params: tuple[object, ...]) -> UUID:
        row = conn.execute(query, params).fetchone()
        assert row is not None
        value: UUID = row[0]
        return value

    tenant = one("INSERT INTO tenants (slug, name) VALUES (%s, %s) RETURNING id", (slug, slug))
    repo = one(
        "INSERT INTO repositories (tenant_id, name, url) VALUES (%s, 'shop', 'git@x:shop') "
        "RETURNING id",
        (tenant,),
    )
    service = one(
        "INSERT INTO services (tenant_id, name, environment, repository_id) "
        "VALUES (%s, 'order-api', 'production', %s) RETURNING id",
        (tenant, repo),
    )
    revision = one(
        "INSERT INTO repo_revisions (tenant_id, repository_id, sha) VALUES (%s, %s, %s) "
        "RETURNING id",
        (tenant, repo, secrets.token_hex(20)),
    )
    release = one(
        "INSERT INTO release_events (tenant_id, service_id, kind, version, revision_id, "
        "idempotency_key, occurred_at) VALUES (%s, %s, 'deploy', 'v1', %s, %s, now()) "
        "RETURNING id",
        (tenant, service, revision, secrets.token_hex(8)),
    )
    source = one(
        "INSERT INTO ingest_sources (tenant_id, name, kind) VALUES (%s, 'file:app.log', 'file') "
        "RETURNING id",
        (tenant,),
    )
    statements: tuple[tuple[str, tuple[object, ...]], ...] = (
        ("INSERT INTO ingest_batches (tenant_id, source_id, batch_id, records) "
         "VALUES (%s, %s, 'b1', 1)", (tenant, source)),
        ("INSERT INTO dead_letters (tenant_id, source_id, reason, raw) VALUES (%s, %s, 'r', 'x')",
         (tenant, source)),
        ("INSERT INTO log_events (tenant_id, service_id, ts, level, message) "
         "VALUES (%s, %s, now(), 'info', 'hello')", (tenant, service)),
        ("INSERT INTO request_windows (tenant_id, service_id, route, window_start, requests, "
         "errors_5xx, errors_4xx, latency) VALUES (%s, %s, '/orders', now(), 1, 0, 0, %s)",
         (tenant, service, ZERO_BUCKETS)),
        ("INSERT INTO exception_windows (tenant_id, service_id, fingerprint, window_start, "
         "occurrences) VALUES (%s, %s, 'fp', now(), 1)", (tenant, service)),
        ("INSERT INTO fingerprints (tenant_id, service_id, fingerprint, exception_type, "
         "sample_message, top_frames, library_only, first_seen, last_seen, occurrences) "
         "VALUES (%s, %s, 'fp', 'E', 'm', '[]', false, now(), now(), 1)", (tenant, service)),
        ("INSERT INTO noise_rules (tenant_id, service_id, fingerprint) VALUES (%s, %s, 'fp')",
         (tenant, service)),
    )  # fmt: skip
    for statement, params in statements:
        conn.execute(statement, params)
    incident = one(
        "INSERT INTO incidents (tenant_id, service_id, severity, title, first_seen, "
        "last_signal_at) VALUES (%s, %s, 'sev3', 'seeded', now(), now()) RETURNING id",
        (tenant, service),
    )
    conn.execute(
        "INSERT INTO incident_signals (tenant_id, incident_id, service_id, kind, key, fired_at, "
        "window_start, window_end) VALUES (%s, %s, %s, 'ERROR_RATE', '/orders', now(), now(), "
        "now())",
        (tenant, incident, service),
    )
    conn.execute(
        "INSERT INTO incident_fingerprints (tenant_id, incident_id, fingerprint, first_seen, "
        "last_seen) VALUES (%s, %s, 'fp', now(), now())",
        (tenant, incident),
    )
    return SeededTenant(tenant, repo, service, revision, release, source, incident)


@pytest.fixture
def two_tenants(admin: psycopg.Connection) -> tuple[SeededTenant, SeededTenant]:
    return seed_tenant(admin), seed_tenant(admin)


@pytest.fixture
def committed_tenant(db_url: str) -> UUID:
    """A tenant committed for tests that need several connections (audit chains)."""
    with psycopg.connect(db_url, autocommit=True) as conn:
        slug = "c-" + secrets.token_hex(5)
        row = conn.execute(
            "INSERT INTO tenants (slug, name) VALUES (%s, %s) RETURNING id", (slug, slug)
        ).fetchone()
    assert row is not None
    tenant: UUID = row[0]
    return tenant
