"""Local environment checks: ``sentinelai doctor``.

Each check reports ok / warn / fail / info with a hint. Only problems that stop Sentinel
running at all are ``fail``. Things needed by later phases (Docker, WSL, Node) are ``warn``.
"""

from __future__ import annotations

import platform
import shutil

# Only fixed argument lists from this module are executed; never a shell, never user input.
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from sentinelai.apps.wiring import Context
from sentinelai.config.secrets import is_private_file

Status = Literal["ok", "warn", "fail", "info"]
Runner = Callable[[Sequence[str], float], tuple[int, str] | None]


class Check(BaseModel):
    name: str
    status: Status
    detail: str
    hint: str | None = None


def run_command(args: Sequence[str], timeout_s: float) -> tuple[int, str] | None:
    """Run a fixed command; ``None`` if it cannot be started or times out."""
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603
            list(args), capture_output=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = completed.stdout or completed.stderr
    # wsl.exe writes UTF-16LE; everything else here is UTF-8/ASCII.
    text = raw.decode("utf-16-le") if b"\x00" in raw else raw.decode("utf-8", "replace")
    return completed.returncode, text.replace("\x00", "").strip()


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def check_python(version: tuple[int, int] = sys.version_info[:2]) -> Check:
    found = f"{version[0]}.{version[1]}"
    if version == (3, 12):
        return Check(name="python", status="ok", detail=f"Python {found}")
    return Check(
        name="python",
        status="fail",
        detail=f"Python {found}; Sentinel AI pins 3.12",
        hint="uv python install 3.12 && uv sync",
    )


def check_uv(which: Callable[[str], str | None] = shutil.which) -> Check:
    if which("uv"):
        return Check(name="uv", status="ok", detail="uv on PATH")
    candidate = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    if candidate.exists():
        return Check(
            name="uv",
            status="warn",
            detail=f"uv installed at {candidate} but not on PATH",
            hint=f"add {candidate.parent} to PATH",
        )
    return Check(
        name="uv",
        status="warn",
        detail="uv not found",
        hint="install from https://docs.astral.sh/uv/",
    )


def check_git(which: Callable[[str], str | None], run: Runner) -> Check:
    result = run(["git", "--version"], 10) if which("git") else None
    if result and result[0] == 0:
        return Check(name="git", status="ok", detail=result[1])
    return Check(name="git", status="fail", detail="git not found", hint="install git")


def check_docker(which: Callable[[str], str | None], run: Runner) -> Check:
    if not which("docker"):
        return Check(
            name="docker",
            status="warn",
            detail="docker CLI not found (needed for Compose from Phase 1, sandbox from Phase 4)",
            hint="install Docker Engine inside a WSL2/Linux host (docs/design/08 section 4)",
        )
    result = run(["docker", "info", "--format", "{{.ServerVersion}} {{.SecurityOptions}}"], 15)
    if result is None or result[0] != 0:
        return Check(
            name="docker",
            status="warn",
            detail="docker CLI found but the daemon is not reachable",
            hint="start the Docker daemon",
        )
    return Check(name="docker", status="ok", detail=f"daemon {result[1][:120]}")


def check_wsl(run: Runner) -> Check:
    result = run(["wsl.exe", "-l", "-q"], 15)
    distros = [line.strip() for line in (result[1] if result else "").splitlines() if line.strip()]
    if result is None or result[0] != 0 or not distros:
        return Check(
            name="wsl",
            status="warn",
            detail="no WSL2 distribution installed",
            hint="wsl --install -d Ubuntu-24.04 (dedicated distro, docs/design/13 PRE-1)",
        )
    return Check(name="wsl", status="ok", detail="distros: " + ", ".join(distros))


def check_node(which: Callable[[str], str | None], run: Runner) -> Check:
    result = run(["node", "--version"], 10) if which("node") else None
    if result and result[0] == 0:
        return Check(name="node", status="ok", detail=f"Node {result[1]}")
    return Check(
        name="node", status="info", detail="Node not found (needed for the dashboard, Phase 2)"
    )


def check_config(ctx: Context) -> Check:
    secrets = ctx.settings.resolved_secrets_file
    if not secrets.exists():
        return Check(
            name="config",
            status="info",
            detail=f"no secrets file at {secrets}",
            hint="sentinelai config init",
        )
    if is_private_file(secrets) is False:
        return Check(
            name="config",
            status="warn",
            detail=f"secrets file {secrets} is readable by other users",
            hint=f"chmod 600 {secrets}",
        )
    return Check(name="config", status="ok", detail=f"secrets file {secrets}")


def local_endpoint_up(url: str, timeout_s: float = 1.5) -> bool:
    try:
        return httpx.get(url, timeout=timeout_s).status_code < 500
    except httpx.HTTPError:
        return False


def check_providers(ctx: Context, probe_local: Callable[[str], bool] = local_endpoint_up) -> Check:
    configured = [
        spec.name
        for spec in ctx.registry.providers.values()
        if spec.api_key_env and ctx.secrets.source(spec.api_key_env)
    ]
    local_up = [
        spec.name
        for spec in ctx.registry.providers.values()
        if not spec.requires_key and probe_local(f"{spec.base_url.rstrip('/')}/models")
    ]
    if not configured and not local_up:
        return Check(
            name="providers",
            status="warn",
            detail="no model provider configured or reachable",
            hint="sentinelai config init, then set GROQ_API_KEY (and a second provider)",
        )
    parts = []
    if configured:
        parts.append("keys: " + ", ".join(configured))
    if local_up:
        parts.append("local up: " + ", ".join(local_up))
    status: Status = "ok" if len(configured) + len(local_up) >= 2 else "warn"
    hint = None if status == "ok" else "add a second provider for independent evaluation (FR-25)"
    return Check(name="providers", status=status, detail="; ".join(parts), hint=hint)


def check_sandbox_host() -> Check:
    if sys.platform == "win32":
        return Check(
            name="sandbox",
            status="info",
            detail="Windows host: sandboxing needs Linux (WSL2 for dev, cloud VM for hostile code)",
            hint="docs/design/04 section 5",
        )
    where = "WSL2" if is_wsl() else platform.system()
    return Check(name="sandbox", status="info", detail=f"{where} host; spike P0-11 not run yet")


def collect_checks(ctx: Context) -> list[Check]:
    which = shutil.which
    checks = [
        Check(name="os", status="info", detail=platform.platform()),
        check_python(),
        check_uv(which),
        check_git(which, run_command),
        check_docker(which, run_command),
    ]
    if sys.platform == "win32":
        checks.append(check_wsl(run_command))
    checks += [
        check_node(which, run_command),
        check_config(ctx),
        check_providers(ctx),
        check_sandbox_host(),
    ]
    return checks
