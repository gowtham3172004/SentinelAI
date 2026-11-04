"""Run Alembic migrations programmatically, each command in one transaction."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from sentinelai.adapters.redaction import register_secret

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def sqlalchemy_url(url: SecretStr | str) -> str:
    """Normalise a libpq-style URL to the psycopg 3 SQLAlchemy dialect; register the password."""
    raw = url.get_secret_value() if isinstance(url, SecretStr) else url
    parsed = make_url(raw)
    if parsed.drivername in {"postgres", "postgresql"}:
        parsed = parsed.set(drivername="postgresql+psycopg")
    register_secret(parsed.password)
    return parsed.render_as_string(hide_password=False)


def _engine(url: SecretStr | str) -> Engine:
    return create_engine(sqlalchemy_url(url), poolclass=NullPool)


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def upgrade(url: SecretStr | str, revision: str = "head") -> None:
    config = _config()
    with _engine(url).begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade(url: SecretStr | str, revision: str) -> None:
    config = _config()
    with _engine(url).begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def current_revision(url: SecretStr | str) -> str | None:
    with _engine(url).connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()
