# 08. External dependencies and their operational requirements

Every dependency is pinned (hash-locked for Python with `uv`, digest-pinned for images). Licence status is **provisional until P0-13** runs an automated licence report. The project licence is Apache-2.0 (OD-08).

## 1. Runtime services

| Dependency | Purpose | Licence | Operational requirements | If unavailable | Phase |
|---|---|---|---|---|---|
| PostgreSQL 16+ | System of record, queue, outbox, audit | PostgreSQL | 2 vCPU / 4 GB min.; disk sized for 7-day telemetry; `pgcrypto`, `vector`; NTP; backups (07 §3) | Sentinel stops processing; the target is unaffected (A5) | 0 |
| pgvector ≥ 0.7 | KB vectors, HNSW, expression indexes | PostgreSQL | Same image (`pgvector/pgvector:pg16`) | KB retrieval falls back to exact and structured layers | 0 |
| OpenTelemetry Collector (contrib) | OTLP intake, file exporter for recorded mode | Apache-2.0 | Bounded retry queue; per-tenant token | Telemetry gap recorded | 1 |
| Docker Engine (rootless) on Linux | Sandbox runners | Apache-2.0 (Moby) | Dedicated host or VM; cgroup v2; seccomp; AppArmor; no shared volumes | Runs park, then escalate | 0 spike, 4 |
| gVisor `runsc` | Tier 2 isolation | Apache-2.0 | Linux host; tests must tolerate unsupported syscalls | Hostile workloads aren't allowed to run | 4 |
| Blob store: local filesystem (MVP) or any S3-compatible (operator's choice) | Prompts, responses, artifacts | n/a | Encrypted disk; backed up with the DB. **Don't bundle MinIO (AGPL).** | Recording fails, so the step fails safe | 2 |
| OIDC provider: Keycloak (dev) or Google / Auth0 / Entra (prod) | Human authentication | Apache-2.0 (Keycloak) | Must support `max_age`/`prompt=login` | No logins; detection continues | 2 |
| Valkey (reference app only) | Target application's queue | BSD-3 | n/a | Reference app degrades | 1 |
| Git host (GitHub first) | Mirror source, PRs, CI | n/a | Read-only mirror credential (codeintel); `sentinel/*` branch + PR credential (deployer) | No mirror refresh; PR-only delivery waits | 1, 6 |
| Weighted reverse proxy (Traefik, decided in Phase 6) | Canary traffic in managed mode | MIT | Dynamic configuration reload | Canary unavailable; staging only | 6 |
| External dead-man's switch (any HTTP heartbeat service) | Independent liveness alert | n/a | Outbound HTTPS | Local watchdog fallback | 2 |

## 2. Model providers (ADR-017)

| Dependency | Purpose | Operational requirements | Risks | Phase |
|---|---|---|---|---|
| **Groq API** (OpenAI-compatible) | Primary fast and strong tiers | API key (secret store); outbound HTTPS to `api.groq.com`; rate limits read from response headers; capability probe at startup | **The free tier has no SLA, enforces per-minute and per-day token and request caps, and retires models often.** A single large request may exceed a model's per-minute token limit. Data terms must be reviewed and recorded before use with real tenant data. | 0 spike, 3 |
| Second provider (decided in P0-12: Gemini API free tier, Cerebras, OpenRouter free models, or local Ollama) | Independent judge; failover; a second system-under-test configuration | The same adapter (OpenAI-compatible) where possible | Free-tier terms may allow training on inputs. Allowed only for the `bench` tenant until reviewed. | 0 spike, 3 |
| Local runtime (Ollama / LM Studio), optional | Offline or private inference, fast tier | **No GPU on the dev machine (OD-14):** CPU-only, small models (around 3B) for fast-tier experiments; not viable for the strong tier | Weaker models lower patch success, which is reported honestly | Optional |

**Feasibility warning (risk RK-01).** At free-tier daily token caps, a full B3 investigation (estimated 100k–400k tokens) may consume most of a model's daily allowance. Consequences, and how the design responds:

1. Run parking with quota-aware scheduling.
2. Investigations spread across several models and providers within an equivalence class.
3. Recorded responses make CI and regression tests free.
4. Benchmark campaigns run over several days.
5. The D2 §18.10 campaign size (~800 runs) **isn't feasible on free tiers alone**. It needs either local inference or a later small paid budget. The decision is deferred until after measurement in Phase 3 (open issue OI-01). **The methodology isn't reduced.** The trial count stays, and campaign duration is what grows.

## 3. Libraries and tools

These are provisional; P0-13 confirms them.

| Area | Choice | Licence | Note |
|---|---|---|---|
| Web/API | FastAPI, Starlette, Uvicorn, Pydantic v2 | MIT / BSD | |
| Database | SQLAlchemy 2, Alembic, psycopg 3 | MIT / MIT / **LGPL-3.0** | psycopg is used as a library without modification: compatible with Apache-2.0 distribution; recorded in NOTICE |
| HTTP | httpx | BSD-3 | No vendor LLM SDKs |
| Crypto | cryptography (Ed25519, HMAC), PyJWT or joserfc for JWS | Apache-2.0/BSD, MIT/BSD | DSSE PAE implemented in-house (small; spec-tested) |
| Passkeys | py_webauthn (server), SimpleWebAuthn (browser) | BSD-3 / MIT | V1 |
| Embeddings | fastembed + onnxruntime; model `BAAI/bge-small-en-v1.5` | Apache-2.0 / MIT / MIT | Model files downloaded once, hash-pinned, cached in the image |
| Code intelligence | git CLI, ripgrep, Python `ast`, tree-sitter + tree-sitter-python | GPL-2.0 (executable only) / MIT / PSF / MIT | git and ripgrep are invoked as executables, not linked |
| Testing | pytest, Hypothesis, coverage, pytest-timeout, Playwright, mutmut | MIT / **MPL-2.0** / Apache / MIT / Apache / ISC | Dev-only; MPL file-level copyleft has no effect on our code |
| Static and security checks (CI and sandbox gates) | ruff, mypy, import-linter, bandit, gitleaks, pip-audit (+ OSV data, CC-BY-4.0) | MIT / MIT / BSD / Apache / MIT / Apache | |
| Semgrep | Semgrep CE engine **or** the Opengrep fork | LGPL-2.1 | **The Semgrep Registry rules use the Semgrep Rules License, which restricts use in competing hosted products.** Use only permissively licensed rule packs plus our own rules. Decided in P0-13. |
| SBOM | syft | Apache-2.0 | |
| Licence check | pip-licenses (Python), license-checker (npm) | MIT / BSD | Enforces the allow-list in CI |
| Dashboard | Next.js, React, TypeScript, openapi-typescript | MIT | |

**Licence allow-list for runtime dependencies:** Apache-2.0, MIT, BSD-2/3, ISC, PSF, PostgreSQL, MPL-2.0 (unmodified), LGPL (dynamically used, unmodified). **Not allowed:** AGPL, SSPL, BUSL, any non-commercial licence, and RSAL in shipped artefacts.

## 4. Developer environment requirements

- Windows 11 host. **All development and runtime happens inside a dedicated WSL2 Ubuntu distro** with systemd, Docker Engine (native), `uv` and Node LTS. The editor is VS Code Remote-WSL.
- The repository should live on the **WSL filesystem** (`~/src/SentinelAI`), not `/mnt/d`, for both performance and the SB-8 isolation rule. Open issue OI-07: you'll need to move the clone.
- Cloud Linux VM (2–4 vCPU, 8 GB, nested virtualisation not required for gVisor's systrap platform) for hostile sandbox workloads, if the spike requires it.
