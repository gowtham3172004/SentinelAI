"""``sentinelai ingest | detect | incidents | serve`` commands.

All commands use SENTINELAI_DATABASE_URL and act as the least-privileged ``r_telemetry`` role
(``SET LOCAL ROLE`` in every transaction), even when the URL is an administrator's.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import psycopg
import typer
from pydantic import SecretStr, ValidationError
from rich.console import Console
from rich.table import Table

from sentinelai.adapters.db.session import connect, tenant_transaction
from sentinelai.adapters.db.telemetry_store import PgTelemetryStore, resolve_tenant
from sentinelai.adapters.redaction import redact
from sentinelai.apps.tail import read_new_lines
from sentinelai.config.settings import Settings
from sentinelai.config.target import TargetConfig, load_target_config
from sentinelai.services.incidents import IncidentManager
from sentinelai.services.ingest import IngestPipeline, IngestPolicy

ingest_app = typer.Typer(help="Ingest telemetry and release events.", no_args_is_help=True)
detect_app = typer.Typer(help="Deterministic incident detection.", no_args_is_help=True)
incidents_app = typer.Typer(help="Inspect incidents.", no_args_is_help=True)
serve_app = typer.Typer(help="Run Sentinel services.", no_args_is_help=True)

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)
ROLE = "r_telemetry"

TargetOption = Annotated[
    Path | None, typer.Option("--target-config", help="The project's sentinel.yaml.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


def _settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        err_console.print(f"[red]configuration error:[/red] {exc}")
        raise typer.Exit(2) from None


def _database_url(settings: Settings) -> SecretStr:
    if settings.database_url is None:
        err_console.print("[red]error:[/red] set SENTINELAI_DATABASE_URL")
        raise typer.Exit(2)
    return settings.database_url


def _open(settings: Settings) -> tuple[psycopg.Connection, UUID]:
    try:
        conn = connect(_database_url(settings))
        return conn, resolve_tenant(conn, settings.tenant)
    except (psycopg.Error, LookupError) as exc:
        err_console.print(f"[red]error:[/red] {redact(str(exc)).splitlines()[0]}")
        raise typer.Exit(1) from None


def _target(path: Path | None) -> TargetConfig:
    try:
        return load_target_config(path)
    except (OSError, ValueError) as exc:
        err_console.print(f"[red]invalid target config:[/red] {exc}")
        raise typer.Exit(2) from None


# --------------------------------------------------------------------------- ingest


@ingest_app.command("file")
def ingest_file(
    path: Annotated[Path, typer.Argument(dir_okay=False, help="JSON lines log file.")],
    target_config: TargetOption = None,
    service: Annotated[str | None, typer.Option(help="Service name if records lack one.")] = None,
    source: Annotated[str | None, typer.Option(help="Source name (default: file:<name>).")] = None,
    follow: Annotated[bool, typer.Option(help="Keep tailing the file.")] = False,
    poll: Annotated[float, typer.Option(help="Seconds between reads when following.")] = 0.5,
) -> None:
    """Ingest a JSON-lines log file from its stored cursor (resumable, rotation-aware)."""
    settings = _settings()
    target = _target(target_config)
    conn, tenant = _open(settings)
    name = source or f"file:{path.name}"
    store = PgTelemetryStore(conn, tenant, role=ROLE)
    pipeline = IngestPipeline(store, mapping=target.field_mapping(),
                              defaults=target.defaults(service=service),
                              policy=IngestPolicy(target.telemetry.info_sample_rate))  # fmt: skip
    totals = [0, 0]
    with conn:
        while True:
            if not path.is_file():
                if not follow:
                    err_console.print(f"[red]error:[/red] {path} does not exist")
                    raise typer.Exit(1)
                time.sleep(poll)  # wait for the application to create its log file
                continue
            chunk = read_new_lines(path, store.source_cursor(name, "file"))
            if chunk.lines or chunk.rotated:
                result = pipeline.ingest_lines(name, "file", chunk.lines, cursor=chunk.cursor)
                totals[0] += result.accepted
                totals[1] += result.rejected
                continue  # drain everything available before sleeping
            if not follow:
                break
            time.sleep(poll)
    console.print(f"{name}: accepted {totals[0]}, rejected {totals[1]}")


@ingest_app.command("events")
def ingest_events(
    path: Annotated[Path, typer.Argument(help="NDJSON deploy/config events.")],
    follow: Annotated[bool, typer.Option(help="Keep watching the file.")] = False,
    poll: Annotated[float, typer.Option(help="Seconds between reads when following.")] = 2.0,
) -> None:
    """Record deploy/config events (idempotent on idempotency_key; re-reading is safe)."""
    settings = _settings()
    conn, tenant = _open(settings)
    pipeline = IngestPipeline(PgTelemetryStore(conn, tenant, role=ROLE))
    with conn:
        while True:
            if path.is_file():
                result = pipeline.ingest_changes(path.read_text(encoding="utf-8").splitlines())
                if result.accepted or not follow:
                    console.print(f"events: recorded {result.accepted} new, "
                                  f"rejected {result.rejected}")  # fmt: skip
            elif not follow:
                err_console.print(f"[red]error:[/red] {path} does not exist")
                raise typer.Exit(1)
            if not follow:
                break
            time.sleep(poll)


# --------------------------------------------------------------------------- detect


@detect_app.command("run")
def detect_run(
    target_config: TargetOption = None,
    once: Annotated[bool, typer.Option(help="Run a single tick and exit.")] = False,
    at: Annotated[str | None, typer.Option(help="Evaluate at this ISO time (replay).")] = None,
    interval: Annotated[float, typer.Option(help="Seconds between ticks.")] = 10.0,
    lag: Annotated[float, typer.Option(help="Seconds to wait for late data.")] = 15.0,
    json_output: JsonOption = False,
) -> None:
    """Evaluate detection rules and manage incidents (one transaction per service per tick)."""
    settings = _settings()
    target = _target(target_config)
    tiers = {target.service: target.route_tiers()} if target.service else {}
    conn, tenant = _open(settings)
    manager = IncidentManager(PgTelemetryStore(conn, tenant, role=ROLE), route_tiers=tiers)
    with conn:
        while True:
            instant = datetime.fromisoformat(at) if at else datetime.now(UTC)
            if not at:
                instant = datetime.fromtimestamp(instant.timestamp() - lag, UTC)
            result = manager.tick(instant)
            summary = {"at": instant.isoformat(), "opened": result.opened,
                       "attached": result.attached, "reopened": result.reopened,
                       "resolved": result.resolved, "suppressed": result.suppressed}  # fmt: skip
            if json_output:
                typer.echo(json.dumps(summary))
            elif any((result.opened, result.attached, result.reopened, result.resolved)):
                console.print(" ".join(f"{k}={v}" for k, v in summary.items() if v))
            if once or at:
                break
            time.sleep(interval)


# --------------------------------------------------------------------------- incidents

FINGERPRINT_KEYS = ("fingerprint", "exception_type", "message", "top_frames",
                    "innermost_in_app", "occurrences", "first_seen")  # fmt: skip
_LIST = (
    "SELECT i.public_id, i.status::text, i.severity::text, s.name, i.title, "
    "i.class_prior::text, i.code_patch_candidate, i.first_seen, i.signal_count "
    "FROM incidents i JOIN services s ON s.id = i.service_id"
)


@incidents_app.command("list")
def incidents_list(
    status: Annotated[str, typer.Option(help="open | all")] = "open",
    json_output: JsonOption = False,
) -> None:
    """List incidents with their deterministic classification prior."""
    settings = _settings()
    conn, tenant = _open(settings)
    where = " WHERE i.status NOT IN ('resolved','closed','suppressed')" if status == "open" else ""
    with conn, tenant_transaction(conn, tenant, role=ROLE):
        rows = conn.execute(_LIST + where + " ORDER BY i.detected_at DESC LIMIT 200").fetchall()
    keys = ("id", "status", "severity", "service", "title", "class_prior", "code_patch_candidate",
            "first_seen", "signals")  # fmt: skip
    items = [dict(zip(keys, row, strict=True)) for row in rows]
    if json_output:
        typer.echo(json.dumps(items, default=str, indent=2))
        return
    table = Table(title=f"incidents ({status})")
    for key in ("id", "severity", "status", "service", "class_prior", "patch?", "title"):
        table.add_column(key)
    for item in items:
        table.add_row(item["id"], item["severity"], item["status"], item["service"],
                      item["class_prior"] or "-", "yes" if item["code_patch_candidate"] else "no",
                      item["title"])  # fmt: skip
    console.print(table)


@incidents_app.command("show")
def incidents_show(
    incident: Annotated[str, typer.Argument(help="Public id, e.g. INC-1000.")],
    json_output: JsonOption = False,
) -> None:
    """Show one incident: classification reasons, signals and fingerprints."""
    settings = _settings()
    conn, tenant = _open(settings)
    with conn, tenant_transaction(conn, tenant, role=ROLE):
        row = conn.execute(
            "SELECT i.id, i.public_id, i.status::text, i.severity::text, s.name, i.title, "
            "i.class_prior::text, i.class_prior_rule, i.class_prior_reasons, "
            "i.code_patch_candidate, i.routes, i.first_seen, i.detected_at, i.last_signal_at, "
            "r.version, r.details->>'git_sha' FROM incidents i "
            "JOIN services s ON s.id = i.service_id "
            "LEFT JOIN release_events r ON r.id = i.suspected_release_id WHERE i.public_id = %s",
            (incident,),
        ).fetchone()
        if row is None:
            err_console.print(f"[red]error:[/red] no incident {incident}")
            raise typer.Exit(1)
        signals = conn.execute(
            "SELECT kind, key, fired_at, observed, baseline FROM incident_signals "
            "WHERE incident_id = %s ORDER BY fired_at DESC LIMIT 20", (row[0],),
        ).fetchall()  # fmt: skip
        fps = conn.execute(
            "SELECT f.fingerprint, f.exception_type, f.sample_message, f.top_frames, "
            "f.innermost_in_app, f.occurrences, f.first_seen FROM incident_fingerprints i "
            "JOIN fingerprints f ON f.fingerprint = i.fingerprint "
            "WHERE i.incident_id = %s ORDER BY f.occurrences DESC", (row[0],),
        ).fetchall()  # fmt: skip
    detail: dict[str, Any] = {
        "id": row[1], "status": row[2], "severity": row[3], "service": row[4], "title": row[5],
        "classification_prior": {"class": row[6], "rule": row[7], "reasons": row[8],
                                 "code_patch_candidate": row[9]},
        "routes": row[10], "first_seen": row[11], "detected_at": row[12],
        "last_signal_at": row[13],
        "suspected_release": {"version": row[14], "git_sha": row[15]} if row[14] else None,
        "signals": [dict(zip(("kind", "key", "fired_at", "observed", "baseline"), s, strict=True))
                    for s in signals],
        "fingerprints": [dict(zip(FINGERPRINT_KEYS, f, strict=True)) for f in fps],
    }  # fmt: skip
    if json_output:
        typer.echo(json.dumps(detail, default=str, indent=2))
        return
    console.print(f"[bold]{detail['id']}[/bold] {detail['severity']} {detail['status']} "
                  f"{detail['service']}: {detail['title']}")  # fmt: skip
    prior = detail["classification_prior"]
    patch = "yes" if prior["code_patch_candidate"] else "no"
    console.print(f"class prior: [bold]{prior['class']}[/bold] ({prior['rule']}); "
                  f"code patch candidate: {patch}")  # fmt: skip
    for reason in prior["reasons"]:
        console.print(f"  - {reason}")
    if detail["suspected_release"]:
        release = detail["suspected_release"]
        console.print(f"suspected release: {release['version']} {release['git_sha'] or ''}")
    for fp in detail["fingerprints"][:5]:
        frames = ", ".join(fp["top_frames"][:2])
        console.print(f"  {fp['exception_type']} x{fp['occurrences']} at {frames or '?'}")


# --------------------------------------------------------------------------- serve


@serve_app.command("ingest")
def serve_ingest(
    target_config: TargetOption = None,
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8090,
) -> None:
    """HTTP ingestion: NDJSON logs, OTLP/HTTP JSON logs, signed release events."""
    import uvicorn  # noqa: PLC0415 - only needed when serving

    from sentinelai.apps.ingest_api import create_ingest_app  # noqa: PLC0415

    settings = _settings()
    if settings.ingest_token is None:
        err_console.print("[red]refusing to start:[/red] set SENTINELAI_INGEST_TOKEN")
        raise typer.Exit(2)
    target = _target(target_config)
    url = _database_url(settings)
    conn, tenant = _open(settings)
    conn.close()
    app = create_ingest_app(
        lambda: connect(url),
        tenant,
        ingest_token=settings.ingest_token,
        release_secret=settings.release_webhook_secret,
        target=target,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
