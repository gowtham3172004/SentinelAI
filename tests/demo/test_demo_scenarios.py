"""AT-DEMO-01..08: the demo target and its three deterministic fault scenarios."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import democtl
from shop.api import create_app
from shop.config import ShopSettings
from tests.demo.conftest import probe, run_pytest

REQUIRED_FIELDS = {"ts", "level", "logger", "message", "service.name", "service.version",
                   "vcs.revision", "deployment.environment"}  # fmt: skip
WITH_NOTE = {"customer_id": "c-1", "items": [{"sku": "SKU-MUG", "qty": 1}], "customer_note": "hi"}
WITHOUT_NOTE = {"customer_id": "c-2", "items": [{"sku": "SKU-TEE", "qty": 2}]}
CLOSED_PORT_DB = "postgresql+psycopg://shop:unused@127.0.0.1:9/shop"


def settings(tmp_path: Path, payment_url: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "database_url": f"sqlite:///{(tmp_path / 'probe.db').as_posix()}",
        "payment_url": payment_url,
        "payment_timeout_s": 2.0,
        "service_version": "test",
    }
    return {**base, **overrides}


def innermost_app_frame(stacktrace: str) -> tuple[str, str]:
    frames = re.findall(r'File "([^"]+)", line \d+, in (\w+)', stacktrace)
    app_frames = [
        (p.replace("\\", "/"), fn) for p, fn in frames if "/shop/" in p.replace("\\", "/")
    ]
    path, function = app_frames[-1]
    return path[path.rindex("/shop/") + 1 :], function


# --------------------------------------------------------------------------- AT-DEMO-01


@pytest.mark.at("AT-DEMO-01")
def test_baseline_suite_passes_in_the_generated_repo(ws: democtl.Workspace) -> None:
    result = run_pytest(ws.repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout


@pytest.mark.at("AT-DEMO-01")
def test_baseline_is_healthy_and_logs_are_structured(
    ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    result = probe(
        ws.repo,
        settings(tmp_path, payment_url),
        [("POST", "/orders", WITH_NOTE), ("POST", "/orders", WITH_NOTE),
         ("POST", "/orders/{last}/pay", None), ("GET", "/orders", None),
         ("GET", "/readyz", None)],
    )  # fmt: skip
    assert [s for _, _, s in result.statuses] == [201, 201, 200, 200, 200]
    assert result.errors() == []
    assert result.logs, "no logs captured"
    for entry in result.logs:
        assert entry.keys() >= REQUIRED_FIELDS, entry
    access = [e for e in result.logs if e["logger"] == "shop.access"]
    assert {e["http.route"] for e in access} >= {"/orders", "/orders/{order_id}/pay"}
    assert all(re.fullmatch(r"[0-9a-f]{32}", e["trace_id"]) for e in access)
    assert all(isinstance(e["duration_ms"], float) for e in access)


# --------------------------------------------------------------------------- AT-DEMO-02/03 (S01)


@pytest.mark.at("AT-DEMO-02")
def test_s01_code_defect_reproduces_with_its_signature(
    ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    democtl.inject(ws, "S01")
    sha, tag = democtl.head(ws)
    assert tag == "v1.1.0"
    deploy = json.loads(ws.events_file.read_text("utf-8").splitlines()[-1])
    assert (deploy["kind"], deploy["version"], deploy["git_sha"]) == ("deploy", "v1.1.0", sha)
    assert deploy["previous_version"] == "v1.0.0"

    result = probe(
        ws.repo,
        settings(tmp_path, payment_url, service_version=tag),
        [("POST", "/orders", WITHOUT_NOTE), ("POST", "/orders", WITH_NOTE),
         ("POST", "/orders", WITHOUT_NOTE)],
    )  # fmt: skip
    assert [s for _, _, s in result.statuses] == [500, 201, 500]  # only note-less orders fail
    errors = result.errors()
    assert len(errors) == 2
    signature = {
        (e["exception.type"], *innermost_app_frame(e["exception.stacktrace"])) for e in errors
    }
    assert signature == {("AttributeError", "shop/orders/normalize.py", "normalize_customer_note")}
    assert "'NoneType' object has no attribute 'strip'" in errors[0]["exception.message"]
    assert errors[0]["service.version"] == "v1.1.0"
    truth = democtl.load_scenarios()["S01"].truth.signature
    assert (truth["exception_type"], truth["function"]) == (
        "AttributeError",
        "normalize_customer_note",
    )


@pytest.mark.at("AT-DEMO-03")
def test_s01_hidden_tests_fail_on_fault_and_pass_with_reference_fix(
    ws: democtl.Workspace, tmp_path: Path
) -> None:
    democtl.inject(ws, "S01")
    bundle = democtl.load_scenarios()["S01"]
    trial = tmp_path / "trial"
    shutil.copytree(ws.repo, trial)  # never put ground truth into the real target repo
    hidden = bundle.directory / "hidden_tests" / "test_note_contract.py"
    shutil.copy(hidden, trial / "tests" / hidden.name)

    failing = run_pytest(trial, f"tests/{hidden.name}")
    assert failing.returncode != 0
    assert "AttributeError" in failing.stdout

    assert bundle.truth.reference_fix is not None
    democtl.git(trial, "apply", str(bundle.directory / bundle.truth.reference_fix))
    passing = run_pytest(trial)  # fail-to-pass and pass-to-pass together
    assert passing.returncode == 0, passing.stdout
    assert "10 passed" in passing.stdout


# --------------------------------------------------------------------------- AT-DEMO-04 (S22)


@pytest.mark.at("AT-DEMO-04")
def test_s22_configuration_fault_reproduces_and_reverting_config_fixes_it(
    ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    baseline_sha = democtl.head(ws)[0]
    democtl.inject(ws, "S22")
    assert democtl.head(ws)[0] == baseline_sha  # no code change at all
    env = democtl.read_env(ws)
    assert env["SHOP_PAYMENT_TIMEOUT_S"] == "0.001"
    change = json.loads(ws.events_file.read_text("utf-8").splitlines()[-1])
    assert (change["kind"], change["changed_keys"]) == ("config_change", ["SHOP_PAYMENT_TIMEOUT_S"])

    requests: list[tuple[str, str, dict[str, Any] | None]] = [
        ("POST", "/orders", WITH_NOTE), ("POST", "/orders/{last}/pay", None),
        ("POST", "/orders", WITHOUT_NOTE), ("POST", "/orders/{last}/pay", None),
    ]  # fmt: skip
    faulty = probe(ws.repo, settings(tmp_path, payment_url, payment_timeout_s=0.001), requests)
    assert [s for _, _, s in faulty.statuses] == [201, 504, 201, 504]
    assert {e["exception.type"] for e in faulty.errors()} == {"shop.payments.PaymentTimeoutError"}

    democtl.reset(ws)
    assert democtl.read_env(ws)["SHOP_PAYMENT_TIMEOUT_S"] == "2.0"
    restored = probe(ws.repo, settings(tmp_path, payment_url, payment_timeout_s=2.0), requests)
    assert [s for _, _, s in restored.statuses] == [201, 200, 201, 200]


# --------------------------------------------------------------------------- AT-DEMO-05 (S33)


@pytest.mark.at("AT-DEMO-05")
def test_s33_database_outage_reproduces_and_restoring_database_fixes_it(
    ws: democtl.Workspace, tmp_path: Path, payment_url: str
) -> None:
    scenario = democtl.load_scenarios()["S33"].scenario
    assert scenario.fault.test_database_url == CLOSED_PORT_DB
    requests: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/healthz", None), ("GET", "/readyz", None), ("POST", "/orders", WITH_NOTE),
        ("GET", "/orders", None),
    ]  # fmt: skip
    down = probe(ws.repo, settings(tmp_path, payment_url, database_url=CLOSED_PORT_DB), requests)
    assert [s for _, _, s in down.statuses] == [200, 503, 503, 503]
    types = {e["exception.type"] for e in down.errors()}
    assert types == {"sqlalchemy.exc.OperationalError"}
    assert any("degraded mode" in e["message"] for e in down.errors())  # logged at startup
    for entry in down.errors():
        # Raised inside the database driver; application code appears only as callers.
        frames = re.findall(r'File "([^"]+)"', entry["exception.stacktrace"])
        assert "/shop/" not in frames[-1].replace("\\", "/"), frames[-1]

    up = probe(ws.repo, settings(tmp_path, payment_url), requests)
    assert [s for _, _, s in up.statuses] == [200, 200, 201, 200]


# --------------------------------------------------------------------------- AT-DEMO-06


@pytest.mark.at("AT-DEMO-06")
def test_traffic_plan_is_deterministic_and_exercises_the_fault_path() -> None:
    plan = democtl.plan_traffic(7, 200)
    assert plan == democtl.plan_traffic(7, 200)
    assert plan != democtl.plan_traffic(8, 200)
    labels = {p.label for p in plan}
    assert {"create_with_note", "create_without_note", "pay_last", "list"} <= labels
    assert plan[0].label == "create_with_note"


@pytest.mark.at("AT-DEMO-06")
def test_generated_repository_is_reproducible(tmp_path: Path) -> None:
    signing = subprocess.run(
        ["git", "config", "--get", "commit.gpgsign"],  # noqa: S607 - git on PATH, as democtl uses
        capture_output=True,
        text=True,
        check=False,
    )
    if signing.stdout.strip().lower() == "true":
        pytest.skip("commit signing is enabled globally; SHAs are intentionally unique")
    shas = []
    for name in ("a", "b"):
        workspace = democtl.Workspace(tmp_path / name)
        baseline = democtl.init(workspace)
        democtl.inject(workspace, "S01")
        shas.append((baseline, democtl.head(workspace)[0]))
    assert shas[0] == shas[1]


@pytest.mark.at("AT-DEMO-06")
def test_traffic_runner_against_the_baseline(tmp_path: Path) -> None:
    app = create_app(
        ShopSettings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}"),
        payment_transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"charge_id": "ch_x"})
        ),
    )
    with TestClient(app) as client:
        counts = democtl.run_traffic(client, democtl.plan_traffic(7, 60))
    assert sum(counts.values()) == 60
    assert all(status in (200, 201) for (_, status) in counts)


# --------------------------------------------------------------------------- AT-DEMO-07


@pytest.mark.at("AT-DEMO-07")
def test_reset_restores_baseline_and_is_idempotent(ws: democtl.Workspace) -> None:
    baseline = ws.state()["baseline_sha"]
    with pytest.raises(democtl.DemoError, match="unknown scenario"):
        democtl.inject(ws, "S99")
    for scenario_id in ("S01", "S22", "S33"):
        democtl.inject(ws, scenario_id)
        with pytest.raises(democtl.DemoError, match="is active"):
            democtl.inject(ws, "S01")
        assert democtl.reset(ws) is True
        assert democtl.head(ws) == (baseline, "v1.0.0")
        assert democtl.git(ws.repo, "tag") == "v1.0.0"
        assert democtl.git(ws.repo, "status", "--porcelain") == ""
        assert democtl.read_env(ws)["SHOP_PAYMENT_TIMEOUT_S"] == "2.0"
        assert ws.state()["scenario"] is None
    events_before = ws.events_file.read_text("utf-8")
    assert democtl.reset(ws) is False  # nothing to undo
    assert ws.events_file.read_text("utf-8") == events_before
    kinds = [json.loads(line)["kind"] for line in events_before.splitlines()]
    assert kinds == ["deploy", "deploy", "rollback", "config_change", "config_change"]


def test_cli_status_and_scenarios(
    ws: democtl.Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert democtl.main(["--workdir", str(ws.root), "--no-compose", "inject", "S01"]) == 0
    assert democtl.main(["--workdir", str(ws.root), "status"]) == 0
    status = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert (status["scenario"], status["version"]) == ("S01", "v1.1.0")
    assert democtl.main(["--workdir", str(ws.root), "--no-compose", "reset"]) == 0
    assert democtl.main(["scenarios"]) == 0
    assert "S33" in capsys.readouterr().out
    assert democtl.main(["--workdir", str(ws.root), "init"]) == 1  # exists, no --force


# --------------------------------------------------------------------------- AT-DEMO-08


@pytest.mark.at("AT-DEMO-08")
def test_scenarios_validate_and_cover_code_config_and_infrastructure() -> None:
    bundles = democtl.load_scenarios()
    assert set(bundles) == {"S01", "S22", "S33"}
    classes = {b.truth.incident_class: b for b in bundles.values()}
    assert set(classes) == {"code_defect", "configuration", "infrastructure"}
    assert classes["code_defect"].truth.code_patch_appropriate
    assert not classes["configuration"].truth.code_patch_appropriate
    assert not classes["infrastructure"].truth.code_patch_appropriate
    for bundle in bundles.values():
        assert bundle.scenario.abstention_expected == (not bundle.truth.code_patch_appropriate)
        assert bundle.scenario.incident_class == bundle.truth.incident_class


@pytest.mark.at("AT-DEMO-08")
def test_no_ground_truth_leaks_into_the_target_repository(ws: democtl.Workspace) -> None:
    democtl.inject(ws, "S01")
    tracked = democtl.git(ws.repo, "ls-files").splitlines()
    assert not any(part in path for path in tracked
                   for part in ("scenario", "ground_truth", "hidden_tests", "reference_fix",
                                "fault.patch"))  # fmt: skip
    history = democtl.git(ws.repo, "log", "--format=%s")
    assert "bug" not in history.lower()
    assert "fault" not in history.lower()
    secret_markers = [b.truth.root_cause[:40] for b in democtl.load_scenarios().values()]
    for path in tracked:
        text = (ws.repo / path).read_text("utf-8", errors="ignore")
        assert not any(marker in text for marker in secret_markers), path
