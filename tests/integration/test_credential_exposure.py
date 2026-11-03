"""Credentials never leave through CLI output, JSON, probe reports, errors, logs or tracebacks.

A hostile local OpenAI-compatible server requires a bearer key and then echoes that key back
in every field it can (content, model name, request ID, model list, error bodies). Every
output channel is then searched for the key. Runs over real HTTP on loopback.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sentinelai.cli.main import app

runner = CliRunner()
KEY_ENV = "LOCALSECURE_KEY"


class EchoingProvider(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _key(self) -> str:
        return (self.headers.get("Authorization") or "").removeprefix("Bearer ")

    def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(200, {"data": [{"id": "echo-model"}, {"id": f"leak-{self._key()}"}]})

    def do_POST(self) -> None:
        key = self._key()
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        if request["model"] == "bad-model":
            self._send(401, {"error": {"message": f"invalid key {key}", "code": key}})
            return
        self._send(
            200,
            {
                "id": f"id-{key}",
                "model": f"model-{key}",
                "choices": [{"message": {"content": f'{{"answer": 5, "k": "{key}"}}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            headers={"x-request-id": f"req-{key}"},
        )


@pytest.fixture
def secret_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "sk-live-" + secrets.token_urlsafe(24)
    monkeypatch.setenv(KEY_ENV, key)
    return key


@pytest.fixture
def server_url(isolated_config: Path) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), EchoingProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    isolated_config.mkdir(parents=True)
    (isolated_config / "providers.yaml").write_text(
        f"providers:\n  secure:\n    base_url: {url}\n    api_key_env: {KEY_ENV}\n",
        encoding="utf-8",
    )
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)  # root, httpx and httpcore at their most verbose
    for name in ("httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


@pytest.mark.at("AT-LLM-04")
@pytest.mark.parametrize(
    "args",
    [
        ["providers", "probe", "secure", "-m", "echo-model", "--json"],
        ["providers", "probe", "secure", "-m", "echo-model"],
        ["providers", "probe", "secure", "-m", "bad-model", "--json"],
        ["providers", "try", "secure", "echo-model", "hi", "--json"],
        ["providers", "try", "secure", "echo-model", "hi"],
        ["providers", "try", "secure", "bad-model", "hi"],
        ["providers", "list", "--json"],
        ["providers", "list"],
        ["doctor", "--json"],
        ["config", "path", "--json"],
    ],
)
def test_no_cli_output_or_log_contains_the_key(
    args: list[str], secret_key: str, server_url: str, debug_logs: pytest.LogCaptureFixture
) -> None:
    result = runner.invoke(app, args)
    assert secret_key not in result.output
    assert secret_key not in debug_logs.text
    assert result.exception is None or isinstance(result.exception, SystemExit)


@pytest.mark.at("AT-LLM-04")
def test_probe_report_file_contains_no_key(
    secret_key: str, server_url: str, tmp_path: Path
) -> None:
    out = tmp_path / "probe.json"
    result = runner.invoke(
        app, ["providers", "probe", "secure", "-m", "echo-model", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    report = out.read_text(encoding="utf-8")
    assert secret_key not in report
    assert "***" in report  # the echoed key was redacted, not silently dropped


def test_typer_never_prints_locals_in_tracebacks() -> None:
    assert app.pretty_exceptions_show_locals is False


@pytest.mark.at("AT-LLM-04")
def test_unexpected_crash_traceback_contains_no_key(secret_key: str, server_url: str) -> None:
    """Force an unexpected exception in the HTTP path in a real subprocess; inspect stderr."""
    script = textwrap.dedent(
        """
        import sys
        from sentinelai.adapters.llm import openai_compatible as oc
        from sentinelai.cli.main import run

        def boom(self, response):
            headers = self._headers()  # a local holding the bearer token
            raise RuntimeError("unexpected failure while parsing")

        oc.OpenAICompatibleProvider._json = boom
        sys.argv = ["sentinelai", "providers", "try", "secure", "echo-model", "hi"]
        run()
        """
    )
    env = {**os.environ, "FORCE_COLOR": "0", "COLUMNS": "200", "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert completed.returncode != 0
    assert "RuntimeError" in completed.stderr  # the crash really happened and was printed
    assert secret_key not in completed.stderr
    assert secret_key not in completed.stdout
