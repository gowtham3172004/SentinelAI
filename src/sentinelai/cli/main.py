"""``sentinelai`` command-line entry point."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from sentinelai import __version__
from sentinelai.apps.wiring import Context
from sentinelai.cli import doctor as doctor_checks
from sentinelai.config.providers import RegistryError
from sentinelai.config.settings import Settings
from sentinelai.domain.llm import ChatMessage, CompletionRequest, ProviderError, Role
from sentinelai.services.probe import FeatureProbe, ProviderProbe, probe_provider

app = typer.Typer(
    help="Sentinel AI: evidence-driven production reliability engineer.",
    no_args_is_help=True,
    add_completion=False,
    # Tracebacks must never print local variables: request headers hold credentials.
    pretty_exceptions_show_locals=False,
)
providers_app = typer.Typer(help="Model providers: list, probe and try.", no_args_is_help=True)
config_app = typer.Typer(help="Configuration and secrets files.", no_args_is_help=True)
app.add_typer(providers_app, name="providers")
app.add_typer(config_app, name="config")

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]
_STATUS_STYLE = {"ok": "green", "warn": "yellow", "fail": "red", "info": "cyan"}


def _context() -> Context:
    try:
        return Context(Settings())
    except (ValidationError, RegistryError) as exc:
        err_console.print(f"[red]configuration error:[/red] {exc}")
        raise typer.Exit(2) from None


def _fail(message: str, code: int = 1) -> typer.Exit:
    err_console.print(f"[red]error:[/red] {message}")
    return typer.Exit(code)


def _yes_no(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def _or_dash(value: object) -> str:
    return "-" if value is None else str(value)


# --------------------------------------------------------------------------- top level


@app.command()
def version() -> None:
    """Print the Sentinel AI version."""
    typer.echo(__version__)


@app.command()
def doctor(json_output: JsonOption = False) -> None:
    """Check the local environment and report what is ready."""
    checks = doctor_checks.collect_checks(_context())
    if json_output:
        typer.echo(json.dumps([c.model_dump() for c in checks], indent=2))
    else:
        table = Table(title="sentinelai doctor", show_lines=False)
        for column in ("check", "status", "detail", "hint"):
            table.add_column(column)
        for check in checks:
            style = _STATUS_STYLE[check.status]
            status = f"[{style}]{check.status.upper()}[/{style}]"
            table.add_row(check.name, status, check.detail, check.hint or "")
        console.print(table)
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(1)


# --------------------------------------------------------------------------- providers


@providers_app.command("list")
def providers_list(json_output: JsonOption = False) -> None:
    """List known providers, whether a key is configured, and suggested models."""
    ctx = _context()
    rows: list[dict[str, Any]] = []
    for spec in ctx.registry.providers.values():
        source = ctx.secrets.source(spec.api_key_env) if spec.api_key_env else None
        rows.append(
            {
                "name": spec.name,
                "base_url": spec.base_url,
                "local": spec.local,
                "requires_key": spec.requires_key,
                "key_env": spec.api_key_env,
                "key_configured": source is not None,
                "key_source": source,
                "trains_on_inputs": spec.terms.trains_on_inputs,
                "suggested_models": [m.model for m in ctx.registry.models_for(spec.name)],
            }
        )
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    table = Table(title="model providers")
    for column in ("provider", "endpoint", "key", "suggested models"):
        table.add_column(column)
    for row in rows:
        if not row["requires_key"]:
            key = "[cyan]local, no key[/cyan]"
        elif row["key_configured"]:
            key = f"[green]set ({row['key_source']})[/green]"
        else:
            key = f"[yellow]missing: {row['key_env']}[/yellow]"
        table.add_row(row["name"], row["base_url"], key, ", ".join(row["suggested_models"]))
    console.print(table)


def _feature(probe: FeatureProbe) -> str:
    if probe.supported is None:
        return "-"
    if not probe.supported:
        return "[yellow]no[/yellow]"
    valid = {True: "valid", False: "invalid output", None: "?"}[probe.valid_output]
    latency = f", {probe.latency_ms} ms" if probe.latency_ms is not None else ""
    return f"[green]yes[/green] ({valid}{latency})"


def _render_probe(report: ProviderProbe) -> None:
    status = "[green]reachable[/green]" if report.reachable else "[red]unreachable[/red]"
    auth = {True: "authenticated", False: "[red]authentication failed[/red]", None: "-"}[
        report.authenticated
    ]
    console.print(
        f"[bold]{report.provider}[/bold]: {status}, {auth}, "
        f"{len(report.listed_models)} models listed"
    )
    if report.error:
        console.print(f"  [red]{report.error}[/red]")
    if not report.models:
        return
    console.print(
        "[dim]A successful probe shows access and output-format compliance only; "
        "it says nothing about model quality.[/dim]"
    )
    table = Table()
    columns = ("model", "listed", "callable", "json_schema", "json_object", "remaining req/tok")
    for column in (*columns, "error"):
        table.add_column(column)
    for probe in report.models:
        limits = probe.rate_limit
        remaining = (
            "-"
            if limits is None
            else f"{_or_dash(limits.remaining_requests)}/{_or_dash(limits.remaining_tokens)}"
        )
        table.add_row(
            probe.model,
            _yes_no(probe.listed),
            _yes_no(probe.callable),
            _feature(probe.json_schema),
            _feature(probe.json_object),
            remaining,
            probe.error or "",
        )
    console.print(table)


@providers_app.command("probe")
def providers_probe(
    provider: Annotated[str, typer.Argument(help="Provider name, e.g. groq, gemini, ollama.")],
    model: Annotated[
        list[str] | None,
        typer.Option("--model", "-m", help="Model to probe (repeatable). Default: suggested."),
    ] = None,
    no_calls: Annotated[
        bool, typer.Option("--no-calls", help="Only check access and list models.")
    ] = False,
    out: Annotated[Path | None, typer.Option(help="Also write the JSON report here.")] = None,
    json_output: JsonOption = False,
) -> None:
    """Check access, list models and test structured-output support (tiny prompts)."""
    ctx = _context()
    models = model or [m.model for m in ctx.registry.models_for(provider)]
    try:
        client = ctx.provider(provider)
    except (ProviderError, RegistryError) as exc:
        raise _fail(str(exc)) from None

    async def go() -> ProviderProbe:
        async with client:
            return await probe_provider(client, models, live_calls=not no_calls)

    report = asyncio.run(go())
    payload = report.model_dump_json(indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if json_output:
        typer.echo(payload)
    else:
        _render_probe(report)
    if not report.reachable or report.authenticated is False:
        raise typer.Exit(1)


@providers_app.command("try")
def providers_try(
    provider: Annotated[str, typer.Argument(help="Provider name.")],
    model: Annotated[str, typer.Argument(help="Model name as the provider spells it.")],
    prompt: Annotated[str, typer.Argument(help="Prompt to send.")],
    max_tokens: Annotated[int, typer.Option(min=1, max=8192)] = 256,
    json_output: JsonOption = False,
) -> None:
    """Send one prompt and show the reply, token usage and remaining quota."""
    ctx = _context()
    try:
        client = ctx.provider(provider)
    except (ProviderError, RegistryError) as exc:
        raise _fail(str(exc)) from None
    request = CompletionRequest(
        model=model,
        messages=(ChatMessage(role=Role.USER, content=prompt),),
        max_output_tokens=max_tokens,
    )

    async def go() -> Any:
        async with client:
            return await client.complete(request)

    try:
        result = asyncio.run(go())
    except ProviderError as exc:
        raise _fail(str(exc)) from None
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(result.content or "[dim](empty reply)[/dim]")
    limits = result.rate_limit
    console.print(
        f"[dim]{result.reported_model or model} | {result.latency_ms} ms | "
        f"tokens in/out {result.usage.input_tokens}/{result.usage.output_tokens} | "
        f"remaining req/tok {limits.remaining_requests}/{limits.remaining_tokens}[/dim]"
    )


# --------------------------------------------------------------------------- config

_SECRETS_TEMPLATE = """\
# Sentinel AI secrets. Keep this file private and never commit it.
# Environment variables with the same names take precedence over this file.
"""

_PROVIDERS_TEMPLATE = """\
# Add or override model providers and models. Example:
#
# providers:
#   together:
#     base_url: https://api.together.xyz/v1
#     api_key_env: TOGETHER_API_KEY
# models:
#   - {provider: groq, model: openai/gpt-oss-120b, family: gpt-oss, tier: strong}
"""


@config_app.command("path")
def config_path(json_output: JsonOption = False) -> None:
    """Show where configuration and secrets are read from."""
    settings = _context().settings
    paths = {
        "config_dir": str(settings.config_dir),
        "secrets_file": str(settings.resolved_secrets_file),
        "providers_file": str(settings.resolved_providers_file),
    }
    if json_output:
        typer.echo(json.dumps(paths, indent=2))
        return
    for key, value in paths.items():
        console.print(f"{key:15} {value}")


@config_app.command("init")
def config_init() -> None:
    """Create the configuration directory and templates (never overwrites)."""
    ctx = _context()
    settings = ctx.settings
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    key_lines = "".join(
        f"# {spec.api_key_env}=\n" for spec in ctx.registry.providers.values() if spec.api_key_env
    )
    files = {
        settings.resolved_secrets_file: _SECRETS_TEMPLATE + key_lines,
        settings.resolved_providers_file: _PROVIDERS_TEMPLATE,
    }
    for path, content in files.items():
        if path.exists():
            console.print(f"exists   {path}")
            continue
        path.write_text(content, encoding="utf-8")
        if os.name == "posix" and path == settings.resolved_secrets_file:
            path.chmod(0o600)
        console.print(f"created  {path}")


def run() -> None:
    app(prog_name="sentinelai")
