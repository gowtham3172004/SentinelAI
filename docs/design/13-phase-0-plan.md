# 13. Phase 0 implementation plan

**Goal:** a foundation every later phase builds on safely, and an early answer to the two assumptions that could invalidate the plan: the sandbox host and free-tier model feasibility.

**Rule:** one task per pull request. Each task lists its acceptance criteria. Nothing is merged without its tests passing in CI.

## 1. Prerequisites (owner actions)

| ID | Action | Why |
|---|---|---|
| PRE-1 | Create a **dedicated WSL2 Ubuntu 24.04 distro** (`sentinel-dev`) with systemd enabled | Dev environment and the sandbox spike (08 §4) |
| PRE-2 | Clone or move the repository into the WSL filesystem (`~/src/SentinelAI`) | Performance; OI-07 |
| PRE-3 | Create a Groq API key; keep it outside the repository (`~/.config/sentinelai/secrets.env`) | S-LLM spike |
| PRE-4 | Choose a second provider account for the spike (Gemini API free tier recommended as a different family), or confirm local Ollama | OI-03 |
| PRE-5 | Create the GitHub repository (private until the licence review passes) | CI |

## 2. Tasks

Ordering: P0-01 → P0-02 → (P0-03, P0-04, P0-05 in parallel) → P0-06 → P0-07 → P0-08 → P0-09. P0-10 to P0-16 run in parallel with these.

| ID | Task | Deliverables | Acceptance criteria |
|---|---|---|---|
| **P0-01** | Repository conventions | Branch `main` (done). `.gitignore` extended (secrets, `.env*`, caches). `.editorconfig`. Conventional-commit guide. PR template with an "AT IDs covered" field. | Template present; gitleaks pre-commit hook blocks a planted fake key |
| **P0-02** | Python project skeleton | `pyproject.toml` (package `sentinelai`, Python 3.12, `uv` lock with hashes, dependency groups `dev` and `test`). `src/sentinelai/{domain,services,adapters,apps,cli}/`. Ruff and mypy strict config. Import-linter contracts (`domain` imports nothing internal; adapters don't import each other; only `apps` wires). pytest with `at` marker. Hypothesis profile. `Makefile` (targets `fmt lint type test up down`). | `make lint type test` green on the skeleton. A deliberate `domain → adapters` import fails import-linter (meta-test). |
| **P0-03** | CI pipeline | GitHub Actions: static stage (ruff, mypy, import-linter, bandit, gitleaks, pip-audit; semgrep/opengrep after P0-13) → unit/property tests → integration with a Postgres service container → AT coverage report (lists the AT IDs exercised) | All stages green on `main`; a failing test blocks merge (branch protection) |
| **P0-04** | Compose skeleton | `docker-compose.yml` with profiles `core` (postgres:16 + pgvector, otel-collector) and `idp` (Keycloak, dev only); healthchecks; `.env.example` | `make up` → all services healthy within 60 s. AT-OPS-08 (partial: core services). |
| **P0-05** | Domain enums and types | `sentinelai.domain.enums` and `.types` mirroring every enum in D2 §13.1 + 02 §4 (including `escalated_dependency_unavailable` and trust level `human`); `RunState`/`Outcome` definitions as data; JSON-schema snapshot tests | Snapshots committed. A test asserts the domain enums equal the Postgres enum values (run after P0-07). |
| **P0-06** | Authority matrix and generator | `db/authority.yaml` (02 §5); `sentinelai.cli.authority` generates (a) SQL grants and (b) a parametrised pytest matrix | Generated files are byte-stable. CI fails if a generated file is stale. |
| **P0-07** | Migration 0001: foundation schema | Alembic set up. Extensions. All enums. `tenants`, `users`, `tenant_memberships`, `services`, `repositories`, `repo_revisions`, `release_events`, `audit_log` + `audit_append()` (02 §3), `forbid_mutation()` + triggers, `jobs`, `outbox_events`, `current_tenant()`, RLS helper applied to the tenant-scoped tables created so far. NOLOGIN group roles + generated grants. Bootstrap of the `default` and `bench` tenants. | **AT-DB-01**, **AT-DB-02** (for the tables present), **AT-DB-03** (`audit_log`), **AT-AUD-01**, **AT-AUD-02**, **AT-AUD-03**, **AT-TEN-01** and **AT-TEN-02** (for the tables present) |
| **P0-08** | `sentinelai audit verify` CLI | Recomputes each chain (or one chain); reports the first break; JSON output for automation | AT-AUD-01/02 run through the CLI as well as SQL |
| **P0-09** | Job queue and outbox | `adapters.db.queue`: claim (`SKIP LOCKED`), lease, heartbeat, reaper, dedupe, `dead` handling. Outbox writer + dispatcher with `NOTIFY` (ADR-022). Graceful-shutdown primitive (stop claiming, finish or release within a deadline). | **AT-Q-01** (16 workers × 10k jobs), **AT-Q-02**, **AT-Q-03**, **AT-Q-04**, AT-OPS-02 (queue part) |
| **P0-10** | Tenancy context plumbing | Session helper sets `SET LOCAL sentinel.tenant_id` per transaction; a system-scoped session type for the queue; job payload validator (IDs only) | AT-TEN-02 through the helper; property test **AT-TEN-03** (initial version) |
| **P0-11** | **S-SBX: sandbox feasibility spike** | `spikes/sandbox/`: scripts that set up rootless Docker in `sentinel-dev`, apply `wsl.conf` (`automount` and `interop` off in that distro), run probes for SB-1 to SB-8 (network, fork bomb, memory bomb, seccomp-denied syscall, AppArmor status, `runsc` run, `/mnt/c` absence). Report `docs/spikes/sandbox-feasibility.md` with pass/fail per control and a decision per 04 §5. | The report exists with evidence (command output) for each control. A decision is recorded as an ADR (WSL2 accepted for which workloads, and whether the cloud VM is required now). |
| **P0-12** | **S-LLM: model provider feasibility spike** | `spikes/llm/`: a minimal throwaway OpenAI-compatible client. Probe of Groq models (list, context window, JSON-schema / json_object / tool support). Rate-limit headers captured. 20 trials each for 3 representative schemas (TriageOutput, HypothesisSet, PatchProposal) on 2–3 candidate strong models: schema validity, repair rate, latency, tokens. Same for the second provider. fastembed load time, memory and throughput on CPU. | Report `docs/spikes/llm-feasibility.md`: capability matrix, measured limits, **estimated runs per day per model**, recommended fast, strong and judge models, data-terms notes per provider. Decisions OI-02 and OI-03 recorded; RK-01 re-assessed. |
| **P0-13** | Licence review | `LICENSE` (Apache-2.0), `NOTICE`, licence allow-list config, pip-licenses check in CI, decision on semgrep vs opengrep and rule packs | **AT-LIC-01** green; the decision recorded in `08` |
| **P0-14** | ADR files 001–012, threat model v0 | `docs/adr/0001…0012` summarising D2 with pointers; `docs/security/threat-model.md` generated from 04 with a review date | Files committed; ADR index links resolve |
| **P0-15** | Scenario spec and pre-registration stub | `bench/schema/scenario.schema.json` (from 06 §C.2), validator CLI, `bench/PREREGISTRATION.md` stub | The validator accepts the S01 example and rejects 5 malformed variants |
| **P0-16** | Developer README and settings schema | `README.md` quickstart (WSL setup, `make up`, tests); `sentinelai.settings` (typed, fail-fast, no secrets in repr) | A fresh clone following the README reaches green tests; invalid settings fail at startup with a clear error |

## 3. Explicitly not in Phase 0

These are deliberately deferred: the reference app (Phase 1), telemetry ingest, anything model-driven beyond the spike, the broker (the spike only probes Docker), and the dashboard.

## 4. Phase 0 exit criteria (gate)

1. P0-01 to P0-16 merged. CI green on `main`, with branch protection on.
2. Passing ATs: AT-DB-01/02/03, AT-AUD-01/02/03, AT-TEN-01/02/03 (foundation tables), AT-Q-01…04, AT-LIC-01, AT-SEC-01, AT-OPS-08 (core).
3. Both spike reports written, with decisions recorded: OI-02, OI-03, OI-04; RK-01 and RK-02 re-assessed.
4. The roadmap re-baselined from the measured Phase 0 pace (10 §2).
5. Gate checklist (10 §5) completed and tag `v0.0` created.

## 5. Progress log

| Date | Task | Status | Evidence |
|---|---|---|---|
| 2026-10-31 | P0-01 Repository conventions | **Done** | `main` branch, `.gitignore` (secrets), `.editorconfig`, `.gitattributes`, PR template, pre-commit config (ruff, gitleaks) |
| 2026-10-31 | P0-02 Python skeleton | **Done** (cross-platform, OD-15) | Layered package; ruff, mypy strict, import-linter contracts (planted violation breaks 2 contracts; verified); pytest with the `at` marker; Hypothesis |
| 2026-10-31 | P0-03 CI pipeline | **Written, not yet run** | `.github/workflows/ci.yml`: static, gitleaks, tests on Ubuntu, Windows and macOS. Runs once the GitHub repository exists (PRE-5). |
| 2026-11-03 | P0-12 Provider feasibility spike | **Done** | [docs/spikes/llm-feasibility.md](../spikes/llm-feasibility.md): Groq and Gemini verified for access and structured-output compliance (**not quality**); Groq 8k TPM ceiling measured; catalogue-vs-callable and Gemini ID-prefix bugs found and fixed; live tests kept opt-in (`-m live`). OI-02/OI-03 remain open pending the benchmark. |
| 2026-11-03 | Credential-exposure audit | **Done** | `tests/integration/test_credential_exposure.py` (AT-LLM-04): hostile echoing provider; no key in stdout, stderr, JSON, probe files, DEBUG logs or crash tracebacks. Two channels found and fixed (httpcore DEBUG response headers; Typer traceback locals). |
| 2026-10-31 | P0-13 Licence | **Partial** | `LICENSE` (Apache-2.0 text from apache.org), `NOTICE`, `pip-audit` clean. The automated licence allow-list check is still to do. |
| 2026-10-31 | P0-16 README and settings | **Partial** | README quickstart (all OSes); typed settings with per-user config directory; `sentinelai doctor` and `config init/path` |
| 2026-11-03 | P0-04 Compose skeleton | **Done** | `docker-compose.yml` profile `core`: Postgres 16 + pgvector 0.8.7, OTel Collector; 127.0.0.1-only ports; password from git-ignored `.env`; restart policy (WSL idle VM stop observed) |
| 2026-11-03 | P0-05 Domain enums | **Done** | `sentinelai.domain.enums`; database-equality test passes |
| 2026-11-03 | P0-06 Authority matrix | **Done** | `adapters/db/authority.yaml` + validated loader + generator; AT-DB-02 checks every cell against the live DB |
| 2026-11-03 | P0-07 Migration 0001 | **Done** | Roles, enums, tenancy (FORCE RLS, fail-closed `current_tenant()`, composite FKs), audit chains, queue and outbox tables. AT-DB-01/02/03, AT-TEN-01/02/03 and AT-AUD-01/02/03 pass. 4 planted defects all caught. |
| 2026-11-03 | P0-08 `audit verify` CLI | **Done** | `sentinelai audit verify [--chain] [--json]`, exit 1 on the first broken row |
| 2026-11-03 | P0-10 Tenancy plumbing | **Done** | `tenant_transaction` / `system_transaction` (transaction-local context; refuse to nest) |
| 2026-11-03 | P0-09 Queue and outbox code | **Done** | `adapters/db/queue.py`: enqueue (dedupe, IDs-only payloads), SKIP LOCKED claim, lease, heartbeat, reaper with `job.dead` alert, worker with graceful stop; outbox with at-least-once dispatch and NOTIFY-on-commit. AT-Q-01 (16 workers × 10,000 jobs, no double claim, 17 s), AT-Q-02/03/04 pass. |

## 6. Order of the first increments after approval

1. **P0-01 + P0-02:** skeleton and tooling. Smallest useful PR; proves the toolchain.
2. **P0-11 and P0-12 started immediately** in parallel. They need your WSL distro and API keys, and their outcome can change later phases.
3. **P0-05 → P0-06 → P0-07 → P0-08:** the schema spine.
4. **P0-09 → P0-10:** queue and tenancy plumbing.
5. **P0-03, P0-04, P0-13 to P0-16:** slotted in as they unblock.
