"""Alembic environment. Migrations run only through ``sentinelai.adapters.db.migrate``.

The caller supplies an open SQLAlchemy connection inside a transaction, so each migration
command is atomic and no database URL is ever written to a config file.
"""

from __future__ import annotations

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "run migrations with `sentinelai db upgrade` (sentinelai.adapters.db.migrate)"
    )

context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
