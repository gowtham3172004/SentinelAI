# Demo target: the "shop" service

A small, realistic order service that Sentinel AI observes and investigates. It exists to exercise the whole Sentinel workflow (detection, investigation, reproduction, patching, verification, and abstention when a code fix is wrong) with **three deterministic faults** and **ground truth** for evaluation.

The demo is deliberately treated as **just another target project**. It ships its own `sentinel.yaml`, emits standard structured JSON logs, and records deploys and config changes as release events. Sentinel contains nothing demo-specific; the same mechanisms apply to any project.

## Architecture

```
                    traffic (democtl traffic, seeded)
                               │ HTTP :8080
┌──────────────────────────────▼───────────────────────────────┐  docker compose --profile demo
│ shop-api  (FastAPI; code mounted from .demo/shop-repo)       │
│   POST /orders · GET /orders[/{id}] · POST /orders/{id}/pay  │──── JSON logs ──► .demo/logs/shop-api.jsonl
│   /healthz (liveness) · /readyz (database)                   │
└──────┬─────────────────────────────────┬─────────────────────┘
       │ SQL                              │ HTTP + W3C traceparent
┌──────▼──────┐                    ┌──────▼───────┐
│ shop-db     │ Postgres 16 tmpfs  │ payment-stub │ approves after 50 ms
└─────────────┘                    └──────────────┘
democtl: deploys/rollbacks/config changes ──► .demo/events.jsonl (release event format)
```

| Path | Role |
|---|---|
| `demo/app/` | The target project. `democtl init` turns it into its own git repo (`.demo/shop-repo`), as a real team's would be |
| `demo/app/tests/` | The project's own tests. Like many real suites, they never send an order without a note, so S01 ships |
| `demo/scenarios/Sxx/` | Fault definition, ground truth, hidden tests and reference fix. **Never copied into the target repo** |
| `demo/democtl.py` | Generate the repo, inject and reset faults, drive seeded traffic, wrap docker compose |

### Telemetry

One JSON object per line, using OpenTelemetry field names:
- **Resource:** `service.name`, `service.version` (release tag), `vcs.revision` (git SHA), `deployment.environment`.
- **Per request:** `trace_id`, `span_id` (W3C `traceparent`, continued into the payment call).
- **Access log:** `http.method`, `http.route` (template), `http.target`, `http.status_code`, `duration_ms`.
- **Errors:** `exception.type`, `exception.message`, `exception.stacktrace`.

## Scenarios

| ID | Fault (how it's injected) | What you observe | Ground truth | Sentinel should |
|---|---|---|---|---|
| **S01** code defect | Release **v1.1.0** is a real commit: a refactor of `normalize_customer_note()` drops the `None` guard | `POST /orders` **without** a note → 500 `AttributeError: 'NoneType' object has no attribute 'strip'`; orders with a note still succeed | code_defect at `shop/orders/normalize.py::normalize_customer_note` | Reproduce → patch → verify → propose |
| **S22** configuration | Config change sets `SHOP_PAYMENT_TIMEOUT_S` from 2.0 to **0.001** (no code change) | `POST /orders/{id}/pay` → 504 `PaymentTimeoutError`; everything else healthy | configuration; revert the setting | **Report only**: no code patch |
| **S33** infrastructure | The `shop-db` container is stopped | Every database endpoint → 503 `OperationalError` raised inside the driver; `/readyz` down, `/healthz` up | infrastructure; restore the database | **Report only**: escalate |

Each scenario directory holds:
- `scenario.yaml` (format: docs/design/06 §C.2)
- `ground_truth.yaml`: class, remediation, whether a code patch is appropriate, root cause, key facts, failure signature
- S01 only: `fault.patch`, `reference_fix.patch` and `hidden_tests/`

The generated repository is **reproducible**: fixed commit identity and dates mean the same SHAs on every machine (unless you sign commits globally).

## Running it

Requirements: Docker with Compose v2.24+, and `uv`. On Windows with Docker inside WSL, set:

```powershell
$env:SENTINELAI_DOCKER = "wsl.exe -d Ubuntu-24.04 -- docker"
```

```sh
uv run python demo/democtl.py init          # .demo/shop-repo at v1.0.0 (+ deploy event)
uv run python demo/democtl.py up            # build and start shop-db, payment-stub, shop-api
uv run python demo/democtl.py traffic       # 200 seeded requests -> all 2xx

uv run python demo/democtl.py inject S01    # or S22, S33 (one scenario at a time)
uv run python demo/democtl.py traffic       # observe the failure pattern
uv run python demo/democtl.py status        # active scenario, version, sha, config

uv run python demo/democtl.py reset         # healthy baseline again (idempotent)
uv run python demo/democtl.py down
```

Expected traffic summaries (seed 7, 60 requests):

| State | create with note | create without note | pay | list/get |
|---|---|---|---|---|
| baseline | 201 | 201 | 200 | 200 |
| S01 | 201 | **500** | 200 | 200 |
| S22 | 201 | 201 | **504** | 200 |
| S33 | **503** | **503** | skipped (no orders) | **503** |

**Reset** returns the repo to `v1.0.0` (removing later tags), restores the baseline config, recreates the containers (the database is tmpfs, so data is clean) and records a rollback or config-revert event. It does nothing if already at baseline.

## Tests

`uv run pytest tests/demo` runs the acceptance tests (AT-DEMO-01..08). They need no Docker: each scenario runs against the generated repo's actual code in a subprocess, with real HTTP to the payment stub.

| Test | Proves |
|---|---|
| AT-DEMO-01 | Baseline suite green in the generated repo; every log line is JSON with the required fields |
| AT-DEMO-02 | S01 reproduces with signature `AttributeError` in `normalize_customer_note`; only note-less orders fail; deploy event matches the commit |
| AT-DEMO-03 | S01 hidden tests fail on the fault and pass with the reference fix, together with the project's own suite |
| AT-DEMO-04 | S22 gives 504 `PaymentTimeoutError` with no code change; reverting the config alone fixes it |
| AT-DEMO-05 | S33 gives 503 `OperationalError` raised in the driver; restoring the database fixes it |
| AT-DEMO-06 | Seeded traffic is deterministic; the generated repo has identical SHAs across runs |
| AT-DEMO-07 | Reset restores the baseline (SHA, tags, config, clean tree) and is idempotent; one scenario at a time |
| AT-DEMO-08 | Scenario files validate; no ground truth, hidden tests or fault hints leak into the target repo |
