"""Demo control for the shop target: generate its git repo, inject or reset faults, drive traffic.

Usage (from the repository root; any OS):
    uv run python demo/democtl.py init            # generate .demo/shop-repo at release v1.0.0
    uv run python demo/democtl.py up              # docker compose --profile demo up
    uv run python demo/democtl.py traffic --count 200
    uv run python demo/democtl.py inject S01      # or S22, S33
    uv run python demo/democtl.py status
    uv run python demo/democtl.py reset           # back to the healthy baseline
    uv run python demo/democtl.py down

On Windows with Docker inside WSL, set SENTINELAI_DOCKER="wsl.exe -d Ubuntu-24.04 -- docker".

Design rules:
- The target repo contains only demo/app. Ground truth, hidden tests and reference fixes stay
  in demo/scenarios and are never copied into it (design/04 TB-9).
- Commits use a fixed identity and dates, so the same scenario always yields the same SHAs.
- One scenario is active at a time. ``reset`` always returns to the baseline and is idempotent.
- Every deploy, rollback and config change is appended to .demo/events.jsonl in the release
  event format (design/02, D2 section 5.7) so Sentinel can correlate onsets with changes.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

DEMO_DIR = Path(__file__).resolve().parent
APP_DIR = DEMO_DIR / "app"
SCENARIOS_DIR = DEMO_DIR / "scenarios"
PROJECT_ROOT = DEMO_DIR.parent
SERVICE = "shop-api"
BASELINE_TAG = "v1.0.0"
BASELINE_ENV = {"SHOP_PAYMENT_TIMEOUT_S": "2.0", "SHOP_ENVIRONMENT": "production"}
CONTAINER_LOG_FILE = "/var/log/shop/shop-api.jsonl"

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Shop Developer",
    "GIT_AUTHOR_EMAIL": "dev@shop.example",
    "GIT_COMMITTER_NAME": "Shop Developer",
    "GIT_COMMITTER_EMAIL": "dev@shop.example",
}
_COMMIT_DATES = {BASELINE_TAG: "2026-10-01T09:00:00+00:00"}
_FAULT_COMMIT_DATE = "2026-10-30T02:10:00+00:00"
_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.db", "*.pyc")


# --------------------------------------------------------------------------- scenario models


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["code", "config", "outage"]
    patch: str | None = None
    commit_message: str | None = None
    release: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    baseline_env: dict[str, str] = Field(default_factory=dict)
    change_summary: str | None = None
    stop_services: list[str] = Field(default_factory=list)
    test_database_url: str | None = None


class Traffic(BaseModel):
    seed: int
    count: int


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^S\d{2}$")
    version: int
    title: str
    category: str
    incident_class: str = Field(alias="class")
    wave: int
    split: Literal["dev", "test"]
    expected_outcome: str
    signature_kind: Literal["exception", "status_code", "duplicate_effect", "metric_threshold"]
    reproducible: bool
    abstention_expected: bool
    fault: Fault
    traffic: Traffic
    red_herrings: list[str] = Field(default_factory=list)
    injection: dict[str, Any] | None = None
    ground_truth_ref: str


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    incident_class: str = Field(alias="class")
    acceptable_classes: list[str]
    remediation: str
    code_patch_appropriate: bool
    root_cause: str
    locations: list[dict[str, str]]
    key_facts: list[str] = Field(min_length=1)
    signature: dict[str, Any]
    reference_fix: str | None = None
    hidden_tests: str | None = None
    recommended_action: str | None = None


@dataclass(frozen=True)
class ScenarioBundle:
    scenario: Scenario
    truth: GroundTruth
    directory: Path


def load_scenarios(directory: Path = SCENARIOS_DIR) -> dict[str, ScenarioBundle]:
    bundles: dict[str, ScenarioBundle] = {}
    for scenario_file in sorted(directory.glob("S*/scenario.yaml")):
        scenario = Scenario.model_validate(yaml.safe_load(scenario_file.read_text("utf-8")))
        truth_file = scenario_file.parent / scenario.ground_truth_ref
        truth = GroundTruth.model_validate(yaml.safe_load(truth_file.read_text("utf-8")))
        bundles[scenario.id] = ScenarioBundle(scenario, truth, scenario_file.parent)
    return bundles


# --------------------------------------------------------------------------- workspace


class DemoError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "shop-repo"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def env_file(self) -> Path:
        return self.runtime / "shop.env"

    @property
    def state_file(self) -> Path:
        return self.runtime / "state.json"

    @property
    def events_file(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            raise DemoError("demo not initialised: run `democtl.py init`")
        data: dict[str, Any] = json.loads(self.state_file.read_text("utf-8"))
        return data

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_file.write_text(json.dumps(state, indent=2) + "\n", "utf-8")


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **_GIT_IDENTITY}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    completed = subprocess.run(  # noqa: S603 - fixed git binary, arguments from this module
        ["git", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args],  # noqa: S607
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DemoError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def head(ws: Workspace) -> tuple[str, str]:
    sha = git(ws.repo, "rev-parse", "HEAD")
    tag = git(ws.repo, "describe", "--tags", "--exact-match", "HEAD")
    return sha, tag


def _emit(ws: Workspace, kind: str, version: str, **details: Any) -> dict[str, Any]:
    event = {
        "kind": kind,
        "service": SERVICE,
        "environment": "production",
        "version": version,
        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "actor": "democtl",
        "idempotency_key": str(uuid.uuid4()),
        **details,
    }
    with ws.events_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
    return event


def _write_env(ws: Workspace, overrides: dict[str, str]) -> None:
    sha, tag = head(ws)
    env = {
        **BASELINE_ENV,
        "SHOP_VERSION": tag,
        "SHOP_GIT_SHA": sha,
        "SHOP_LOG_FILE": CONTAINER_LOG_FILE,
        **overrides,
    }
    ws.env_file.write_text("".join(f"{k}={v}\n" for k, v in sorted(env.items())), "utf-8")


def read_env(ws: Workspace) -> dict[str, str]:
    lines = ws.env_file.read_text("utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


# --------------------------------------------------------------------------- operations


def init(ws: Workspace, *, force: bool = False) -> str:
    if ws.root.exists():
        if not force:
            raise DemoError(f"{ws.root} exists: use --force to recreate it")
        shutil.rmtree(ws.root)
    shutil.copytree(APP_DIR, ws.repo, ignore=_IGNORE)
    for directory in (ws.runtime, ws.logs):
        directory.mkdir(parents=True, exist_ok=True)
    git(ws.repo, "init", "-q")
    git(ws.repo, "add", "-A")
    git(ws.repo, "commit", "-q", "-m", f"Release {BASELINE_TAG}", date=_COMMIT_DATES[BASELINE_TAG])
    git(ws.repo, "tag", BASELINE_TAG)
    sha, _ = head(ws)
    _write_env(ws, {})
    ws.save_state({"scenario": None, "baseline_sha": sha})
    _emit(ws, "deploy", BASELINE_TAG, git_sha=sha, previous_version=None)
    return sha


def inject(ws: Workspace, scenario_id: str) -> ScenarioBundle:
    bundles = load_scenarios()
    if scenario_id not in bundles:
        raise DemoError(f"unknown scenario {scenario_id}; known: {', '.join(bundles)}")
    state = ws.state()
    if state["scenario"] is not None:
        raise DemoError(f"scenario {state['scenario']} is active: run `reset` first")
    bundle = bundles[scenario_id]
    fault = bundle.scenario.fault
    if fault.type == "code":
        if not (fault.patch and fault.commit_message and fault.release):
            raise DemoError(f"{scenario_id}: code faults need patch, commit_message and release")
        _, previous = head(ws)
        git(ws.repo, "apply", "--whitespace=nowarn", str(bundle.directory / fault.patch))
        git(ws.repo, "commit", "-q", "-am", fault.commit_message, date=_FAULT_COMMIT_DATE)
        git(ws.repo, "tag", fault.release)
        sha, _ = head(ws)
        _write_env(ws, {})
        _emit(ws, "deploy", fault.release, git_sha=sha, previous_version=previous)
    elif fault.type == "config":
        _write_env(ws, fault.env)
        _, version = head(ws)
        _emit(ws, "config_change", version, changed_keys=sorted(fault.env))
    ws.save_state({**state, "scenario": scenario_id})
    return bundle


def reset(ws: Workspace) -> bool:
    """Return to the healthy baseline. Returns False if there was nothing to undo."""
    state = ws.state()
    active = state["scenario"]
    if active is None and head(ws)[0] == state["baseline_sha"]:
        return False
    _, version = head(ws)
    git(ws.repo, "reset", "-q", "--hard", BASELINE_TAG)
    git(ws.repo, "clean", "-q", "-fd")
    for tag in git(ws.repo, "tag").splitlines():
        if tag != BASELINE_TAG:
            git(ws.repo, "tag", "-d", tag)
    _write_env(ws, {})
    fault = load_scenarios()[active].scenario.fault if active else None
    if fault is not None and fault.type == "code":
        _emit(ws, "rollback", BASELINE_TAG, git_sha=state["baseline_sha"], previous_version=version)
    elif fault is not None and fault.type == "config":
        _emit(ws, "config_change", BASELINE_TAG, changed_keys=sorted(fault.env))
    ws.save_state({**state, "scenario": None})
    return True


# --------------------------------------------------------------------------- traffic

_NOTES = ["Leave at the door", "Gift wrap please", "Call on arrival", "  ring   twice ", "Fragile"]
_SKUS = ["SKU-MUG", "SKU-TEE", "SKU-CAP", "SKU-BAG"]


@dataclass(frozen=True)
class PlannedRequest:
    label: str
    method: str
    path: str
    body: dict[str, Any] | None = None


def plan_traffic(seed: int, count: int) -> list[PlannedRequest]:
    """A deterministic request mix: orders with and without notes, payments and reads."""
    rng = random.Random(seed)  # noqa: S311 - deterministic test traffic, not cryptography
    plan: list[PlannedRequest] = []
    for index in range(count):
        roll = rng.random()
        items = [{"sku": rng.choice(_SKUS), "qty": rng.randint(1, 3)}]
        body: dict[str, Any] = {"customer_id": f"c-{rng.randint(1, 500)}", "items": items}
        if roll < 0.45:
            body["customer_note"] = rng.choice(_NOTES)
            plan.append(PlannedRequest("create_with_note", "POST", "/orders", body))
        elif roll < 0.70:
            plan.append(PlannedRequest("create_without_note", "POST", "/orders", body))
        elif roll < 0.85:
            plan.append(PlannedRequest("pay_last", "POST", "/orders/{last}/pay"))
        elif roll < 0.95:
            plan.append(PlannedRequest("list", "GET", "/orders"))
        else:
            plan.append(PlannedRequest("get_last", "GET", "/orders/{last}"))
        if index == 0 and plan[0].label != "create_with_note":
            plan[0] = PlannedRequest("create_with_note", "POST", "/orders",
                                     {**body, "customer_note": _NOTES[0]})  # fmt: skip
    return plan


def run_traffic(
    client: httpx.Client, plan: list[PlannedRequest], *, rate_per_s: float = 0
) -> Counter[tuple[str, int]]:
    """Send ``plan`` and count (label, status). ``{last}`` is the most recent created order."""
    results: Counter[tuple[str, int]] = Counter()
    last: int | None = None
    for request in plan:
        if "{last}" in request.path and last is None:
            results[(request.label, 0)] += 1  # skipped: nothing created yet
            continue
        path = request.path.replace("{last}", str(last))
        try:
            response = client.request(request.method, path, json=request.body)
            status = response.status_code
        except httpx.HTTPError:
            status = -1  # connection failure
        if request.method == "POST" and path == "/orders" and status == 201:
            last = int(response.json()["id"])
        results[(request.label, status)] += 1
        if rate_per_s > 0:
            time.sleep(1 / rate_per_s)
    return results


# --------------------------------------------------------------------------- docker compose


def compose(*args: str) -> None:
    docker = shlex.split(os.environ.get("SENTINELAI_DOCKER", "docker"))
    command = [*docker, "compose", "--profile", "demo", *args]
    if subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode != 0:  # noqa: S603
        raise DemoError(f"command failed: {' '.join(command)}")


def _compose_after_inject(bundle: ScenarioBundle) -> None:
    fault = bundle.scenario.fault
    if fault.type == "outage":
        compose("stop", *fault.stop_services)
    else:
        compose("up", "-d", "--force-recreate", "--no-deps", "shop-api")


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="democtl", description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", type=Path, default=PROJECT_ROOT / ".demo")
    parser.add_argument("--no-compose", action="store_true", help="Skip docker compose actions.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").add_argument("--force", action="store_true")
    sub.add_parser("up")
    sub.add_parser("down")
    sub.add_parser("inject").add_argument("scenario")
    sub.add_parser("reset")
    sub.add_parser("status")
    sub.add_parser("scenarios")
    traffic = sub.add_parser("traffic")
    traffic.add_argument("--url", default="http://127.0.0.1:8080")
    traffic.add_argument("--seed", type=int, default=7)
    traffic.add_argument("--count", type=int, default=200)
    traffic.add_argument("--rate", type=float, default=10.0, help="Requests per second.")
    args = parser.parse_args(argv)
    ws = Workspace(args.workdir)
    try:
        if args.command == "init":
            print(f"target repo at {BASELINE_TAG}: {init(ws, force=args.force)}")
        elif args.command == "up":
            compose("up", "-d", "--build", "--wait")
        elif args.command == "down":
            compose("down")
        elif args.command == "inject":
            bundle = inject(ws, args.scenario)
            if not args.no_compose:
                _compose_after_inject(bundle)
            print(f"injected {bundle.scenario.id}: {bundle.scenario.title}")
        elif args.command == "reset":
            changed = reset(ws)
            if not args.no_compose:
                compose("up", "-d", "--force-recreate", "shop-db", "payment-stub", "shop-api")
            print("reset to baseline" if changed else "already at baseline")
        elif args.command == "status":
            sha, tag = head(ws)
            print(json.dumps({"scenario": ws.state()["scenario"], "version": tag, "sha": sha,
                              "env": read_env(ws)}, indent=2))  # fmt: skip
        elif args.command == "scenarios":
            for bundle in load_scenarios().values():
                s = bundle.scenario
                print(f"{s.id}  {s.incident_class:<15} expected={s.expected_outcome:<16} {s.title}")
        elif args.command == "traffic":
            with httpx.Client(base_url=args.url, timeout=10) as client:
                counts = run_traffic(client, plan_traffic(args.seed, args.count),
                                     rate_per_s=args.rate)  # fmt: skip
            for (label, status), n in sorted(counts.items()):
                print(f"{label:<22} {status:>4}  x{n}")
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
