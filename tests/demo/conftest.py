"""Fixtures for the demo target: generated workspace, real payment stub, request probes.

Probes run the *generated repository's* code (baseline, fault or fix) in a subprocess, so a
test exercises exactly what would be deployed, and module caching can't hide a change.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn

import democtl
from shop.payment_stub import app as payment_app


@pytest.fixture
def ws(tmp_path: Path) -> democtl.Workspace:
    workspace = democtl.Workspace(tmp_path / ".demo")
    democtl.init(workspace)
    return workspace


@pytest.fixture(scope="session")
def payment_url() -> Iterator[str]:
    """The real payment stub served over HTTP (about 50 ms latency) on a free port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(payment_app, host="127.0.0.1", port=port,
                                           log_level="warning"))  # fmt: skip
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("payment stub did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


_PROBE = textwrap.dedent(
    """
    import json, sys
    from fastapi.testclient import TestClient
    from shop.api import create_app
    from shop.config import ShopSettings

    spec = json.loads(sys.argv[1])
    settings = ShopSettings(**spec["settings"])
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        results = []
        last = None
        for method, path, body in spec["requests"]:
            if "{last}" in path:
                path = path.replace("{last}", str(last))
            response = client.request(method, path, json=body)
            if path == "/orders" and method == "POST" and response.status_code == 201:
                last = response.json()["id"]
            results.append([method, path, response.status_code])
    print("PROBE-RESULT " + json.dumps(results))
    """
)


@dataclass
class ProbeResult:
    statuses: list[tuple[str, str, int]]
    logs: list[dict[str, Any]]
    raw_stdout: str

    def errors(self) -> list[dict[str, Any]]:
        return [entry for entry in self.logs if entry.get("level") == "error"]


def probe(
    repo: Path, settings: dict[str, Any], requests: list[tuple[str, str, dict[str, Any] | None]]
) -> ProbeResult:
    spec = json.dumps({"settings": settings, "requests": requests})
    env = {**os.environ, "PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _PROBE, spec],
        cwd=repo,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    logs: list[dict[str, Any]] = []
    statuses: list[tuple[str, str, int]] = []
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE-RESULT "):
            items = json.loads(line.removeprefix("PROBE-RESULT "))
            statuses = [(str(m), str(p), int(s)) for m, p, s in items]
        elif line.startswith("{"):
            logs.append(json.loads(line))  # every log line must be valid JSON
    return ProbeResult(statuses, logs, completed.stdout)


def run_pytest(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(  # noqa: S603 - fixed interpreter
        [sys.executable, "-m", "pytest", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
