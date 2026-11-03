"""End-to-end: the real CLI talks over real HTTP to a local OpenAI-compatible server."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sentinelai.cli.main import app

runner = CliRunner()


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    """``model-a`` supports json_schema; ``model-b`` rejects it but supports json_object."""

    seen_auth: list[str | None] = []  # noqa: RUF012 - test-only shared record

    def log_message(self, format: str, *args: Any) -> None:  # silence test output
        return

    def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        FakeOpenAIHandler.seen_auth.append(self.headers.get("Authorization"))
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": "model-a"}, {"id": "model-b"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        FakeOpenAIHandler.seen_auth.append(self.headers.get("Authorization"))
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        fmt = (request.get("response_format") or {}).get("type")
        if request["model"] not in {"model-a", "model-b"}:
            self._send(404, {"error": {"message": f"model {request['model']} does not exist"}})
            return
        if request["model"] == "model-b" and fmt == "json_schema":
            self._send(400, {"error": {"message": "response_format json_schema not supported"}})
            return
        content = '{"answer": 5}' if fmt else "Hello from the local test server."
        self._send(
            200,
            {
                "id": "cmpl-local",
                "model": request["model"],
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
            headers={"x-ratelimit-remaining-requests": "99", "x-ratelimit-remaining-tokens": "999"},
        )


@pytest.fixture
def local_server() -> Iterator[str]:
    FakeOpenAIHandler.seen_auth = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def configured(local_server: str, isolated_config: Path) -> str:
    isolated_config.mkdir(parents=True)
    (isolated_config / "providers.yaml").write_text(
        f"providers:\n  localtest:\n    base_url: {local_server}\n", encoding="utf-8"
    )
    return local_server


@pytest.mark.at("AT-LLM-09")
def test_probe_local_provider_end_to_end(configured: str, tmp_path: Path) -> None:
    out = tmp_path / "probe.json"
    result = runner.invoke(
        app,
        [
            "providers",
            "probe",
            "localtest",
            "-m",
            "model-a",
            "-m",
            "model-b",
            "-m",
            "gone",
            "--out",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert json.loads(out.read_text(encoding="utf-8")) == report
    assert report["reachable"] is True
    assert report["listed_models"] == ["model-a", "model-b"]
    by_model = {m["model"]: m for m in report["models"]}
    assert by_model["model-a"]["json_schema"]["supported"] is True
    assert by_model["model-a"]["json_schema"]["valid_output"] is True
    assert by_model["model-b"]["json_schema"]["supported"] is False
    assert by_model["model-b"]["json_object"]["supported"] is True
    assert by_model["model-a"]["rate_limit"]["remaining_tokens"] == 999
    assert by_model["gone"]["listed"] is False
    assert set(FakeOpenAIHandler.seen_auth) == {None}  # local provider: no credentials sent


def test_probe_human_output(configured: str) -> None:
    result = runner.invoke(app, ["providers", "probe", "localtest", "-m", "model-a"])
    assert result.exit_code == 0, result.output
    assert "reachable" in result.stdout
    assert "model-a" in result.stdout


def test_try_local_provider(configured: str) -> None:
    result = runner.invoke(app, ["providers", "try", "localtest", "model-a", "hello", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["content"] == "Hello from the local test server."
    assert payload["usage"] == {"input_tokens": 12, "output_tokens": 4}


def test_try_reports_provider_errors(configured: str) -> None:
    result = runner.invoke(app, ["providers", "try", "localtest", "nope", "hello"])
    assert result.exit_code == 1
    assert "HTTP 404" in result.output


def test_unreachable_local_provider_fails_cleanly(isolated_config: Path) -> None:
    isolated_config.mkdir(parents=True)
    (isolated_config / "providers.yaml").write_text(
        "providers:\n  dead:\n    base_url: http://127.0.0.1:9/v1\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["providers", "probe", "dead", "--no-calls"])
    assert result.exit_code == 1
    assert "unreachable" in result.stdout
