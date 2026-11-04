"""``sentinelai db`` and ``sentinelai audit`` commands.

Both use SENTINELAI_ADMIN_DATABASE_URL: an owner or administrator connection, never a
service's. The URL and its password are never printed.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

import psycopg
import typer
from pydantic import SecretStr, ValidationError
from rich.console import Console
from sqlalchemy.exc import SQLAlchemyError

from sentinelai.adapters.db import migrate
from sentinelai.adapters.db.authority import grants_sql, load_matrix
from sentinelai.adapters.db.session import audit_chain_ids, connect, verify_chain
from sentinelai.adapters.redaction import redact
from sentinelai.config.settings import Settings

db_app = typer.Typer(help="Database schema: migrations and grants.", no_args_is_help=True)
audit_app = typer.Typer(help="Tamper-evident audit log.", no_args_is_help=True)

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)
JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


def _admin_url() -> SecretStr:
    try:
        url = Settings().admin_database_url
    except ValidationError as exc:
        err_console.print(f"[red]configuration error:[/red] {exc}")
        raise typer.Exit(2) from None
    if url is None:
        err_console.print(
            "[red]error:[/red] set SENTINELAI_ADMIN_DATABASE_URL "
            "(postgresql://owner:...@host:port/dbname)"
        )
        raise typer.Exit(2)
    return url


def _db_error(exc: Exception) -> typer.Exit:
    err_console.print(f"[red]database error:[/red] {redact(str(exc)).splitlines()[0]}")
    return typer.Exit(1)


@db_app.command("upgrade")
def db_upgrade(revision: Annotated[str, typer.Option(help="Target revision.")] = "head") -> None:
    """Apply migrations (each command runs in one transaction)."""
    url = _admin_url()
    try:
        migrate.upgrade(url, revision)
        console.print(f"database at revision {migrate.current_revision(url)}")
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise _db_error(exc) from None


@db_app.command("downgrade")
def db_downgrade(
    revision: Annotated[str, typer.Option(help="Target revision, e.g. base.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm a destructive downgrade.")] = False,
) -> None:
    """Revert migrations. Destroys data in dropped tables; requires --yes."""
    if not yes:
        err_console.print("[red]refusing:[/red] downgrade drops data; re-run with --yes")
        raise typer.Exit(2)
    url = _admin_url()
    try:
        migrate.downgrade(url, revision)
        console.print(f"database at revision {migrate.current_revision(url)}")
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise _db_error(exc) from None


@db_app.command("current")
def db_current() -> None:
    """Show the current schema revision."""
    url = _admin_url()
    try:
        console.print(migrate.current_revision(url) or "(empty database)")
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise _db_error(exc) from None


@db_app.command("grants-sql")
def db_grants_sql(
    table: Annotated[list[str] | None, typer.Option(help="Limit to these tables.")] = None,
) -> None:
    """Print GRANT/REVOKE statements generated from the authority matrix."""
    matrix = load_matrix()
    unknown = sorted(set(table or []) - set(matrix.tables))
    if unknown:
        err_console.print(f"[red]error:[/red] not in the authority matrix: {', '.join(unknown)}")
        raise typer.Exit(2)
    typer.echo(grants_sql(matrix, table), nl=False)


@audit_app.command("verify")
def audit_verify(
    chain: Annotated[UUID | None, typer.Option(help="Verify one chain (tenant id).")] = None,
    json_output: JsonOption = False,
) -> None:
    """Recompute audit hash chains; exit 1 at the first broken row of any chain."""
    url = _admin_url()
    try:
        with connect(url) as conn:
            chains = [chain] if chain else audit_chain_ids(conn)
            reports = [verify_chain(conn, chain_id) for chain_id in chains]
    except psycopg.Error as exc:
        raise _db_error(exc) from None
    if json_output:
        payload = [
            {
                "chain_id": str(r.chain_id),
                "rows": r.rows,
                "ok": r.ok,
                "head_hash": r.head_hash,
                "first_break": (
                    {"row_id": r.first_break.row_id, "reason": r.first_break.reason}
                    if r.first_break
                    else None
                ),
            }
            for r in reports
        ]
        typer.echo(json.dumps(payload, indent=2))
    else:
        for r in reports:
            if r.first_break is None:
                console.print(
                    f"[green]OK[/green]    {r.chain_id}  rows={r.rows}  head={r.head_hash}"
                )
            else:
                console.print(
                    f"[red]BROKEN[/red] {r.chain_id}  rows={r.rows}  first break at row "
                    f"{r.first_break.row_id} ({r.first_break.reason})"
                )
        if not reports:
            console.print("no audit chains yet")
    if any(not r.ok for r in reports):
        raise typer.Exit(1)
