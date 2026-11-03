from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sentinelai import __version__
from sentinelai.apps.wiring import Context
from sentinelai.cli import doctor
from sentinelai.cli.main import app
from sentinelai.config.settings import Settings

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_providers_list_reports_key_presence_without_revealing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret_value")
    result = runner.invoke(app, ["providers", "list", "--json"])
    assert result.exit_code == 0
    assert "gsk_super_secret_value" not in result.stdout
    rows = {row["name"]: row for row in json.loads(result.stdout)}
    assert rows["groq"]["key_configured"] is True
    assert rows["groq"]["key_source"] == "env"
    assert rows["gemini"]["key_configured"] is False
    assert rows["ollama"]["requires_key"] is False


def test_providers_list_human_output_works() -> None:
    result = runner.invoke(app, ["providers", "list"])
    assert result.exit_code == 0
    assert "groq" in result.stdout


def test_probe_without_key_explains_how_to_fix() -> None:
    result = runner.invoke(app, ["providers", "probe", "groq"])
    assert result.exit_code == 1
    assert "GROQ_API_KEY" in result.output


def test_probe_unknown_provider() -> None:
    result = runner.invoke(app, ["providers", "probe", "nope"])
    assert result.exit_code == 1
    assert "unknown provider" in result.output


def test_config_init_creates_templates_and_never_overwrites(isolated_config: Path) -> None:
    first = runner.invoke(app, ["config", "init"])
    assert first.exit_code == 0
    secrets = isolated_config / "secrets.env"
    assert "# GROQ_API_KEY=" in secrets.read_text(encoding="utf-8")
    secrets.write_text("GROQ_API_KEY=keep-me\n", encoding="utf-8")

    second = runner.invoke(app, ["config", "init"])
    assert second.exit_code == 0
    assert "exists" in second.stdout
    assert secrets.read_text(encoding="utf-8") == "GROQ_API_KEY=keep-me\n"


def test_secrets_file_keys_are_picked_up(isolated_config: Path) -> None:
    isolated_config.mkdir(parents=True)
    (isolated_config / "secrets.env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    result = runner.invoke(app, ["providers", "list", "--json"])
    rows = {row["name"]: row for row in json.loads(result.stdout)}
    assert rows["gemini"]["key_source"] == "file"


def test_config_path_json(isolated_config: Path) -> None:
    result = runner.invoke(app, ["config", "path", "--json"])
    assert json.loads(result.stdout)["config_dir"] == str(isolated_config)


def test_invalid_providers_file_is_a_configuration_error(isolated_config: Path) -> None:
    isolated_config.mkdir(parents=True)
    (isolated_config / "providers.yaml").write_text(
        "providers:\n  x:\n    base_url: http://remote.example\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["providers", "list"])
    assert result.exit_code == 2
    assert "configuration error" in result.output


def test_doctor_json_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = [
        doctor.Check(name="python", status="ok", detail="3.12"),
        doctor.Check(name="docker", status="warn", detail="missing", hint="install"),
    ]
    monkeypatch.setattr(doctor, "collect_checks", lambda _ctx: checks)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert [c["name"] for c in json.loads(result.stdout)] == ["python", "docker"]

    failing = [*checks, doctor.Check(name="git", status="fail", detail="missing")]
    monkeypatch.setattr(doctor, "collect_checks", lambda _ctx: failing)
    assert runner.invoke(app, ["doctor"]).exit_code == 1


# ------------------------------------------------------------------ individual doctor checks


def test_cli_source_strings_are_ascii_for_every_terminal() -> None:
    """Legacy Windows consoles (cp1252, cp437) mangle non-ASCII; keep CLI output ASCII."""
    cli_dir = Path(doctor.__file__).parent
    for source in cli_dir.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert text.isascii(), f"non-ASCII character in {source.name}"


def test_check_python() -> None:
    assert doctor.check_python((3, 12)).status == "ok"
    assert doctor.check_python((3, 11)).status == "fail"


def test_check_git_and_docker_with_fakes() -> None:
    def which(name: str) -> str | None:
        return f"/bin/{name}" if name in {"git", "docker"} else None

    def run(args: Sequence[str], _timeout: float) -> tuple[int, str] | None:
        return (0, "git version 2.46") if args[0] == "git" else (1, "Cannot connect")

    assert doctor.check_git(which, run).status == "ok"
    docker = doctor.check_docker(which, run)
    assert docker.status == "warn"
    assert "daemon" in docker.detail
    assert doctor.check_docker(lambda _n: None, run).status == "warn"


def test_check_wsl_parses_distro_list() -> None:
    assert doctor.check_wsl(lambda _a, _t: (0, "Ubuntu-24.04\nsentinel-dev\n")).status == "ok"
    assert doctor.check_wsl(lambda _a, _t: (0, "")).status == "warn"
    assert doctor.check_wsl(lambda _a, _t: None).status == "warn"


def test_check_providers() -> None:
    none = doctor.check_providers(Context(Settings(), environ={}), probe_local=lambda _u: False)
    assert none.status == "warn"
    one = doctor.check_providers(
        Context(Settings(), environ={"GROQ_API_KEY": "k"}), probe_local=lambda _u: False
    )
    assert one.status == "warn"
    assert "second provider" in (one.hint or "")
    two = doctor.check_providers(
        Context(Settings(), environ={"GROQ_API_KEY": "k"}),
        probe_local=lambda url: "11434" in url,
    )
    assert two.status == "ok"
    assert "ollama" in two.detail
