# 09. Scope: MVP, V1 and later

Supersedes the tier lists in D1 §7 and §14 where they differ. **Every original item is listed below. Nothing is dropped.** The D1 §14 cut line still applies, unchanged: *never* drop the sandbox security model, signed approvals, the failure-signature rule or the abstention scenarios. The following are added to that protected list: tenant isolation enforcement, the egress policy, and broker-side signature recomputation.

## 0. Lean MVP, the current target (OD-16, ADR-023)

**Sections 1–3 below are the v1.1 plan. Where they conflict with ADR-023, ADR-023 wins.**

"Ready" means a user can do all of the following on one machine with Docker Compose:
1. Run the reference app with an injected fault.
2. Watch Sentinel detect the incident.
3. Read an evidence-cited investigation, including the rejected rival hypotheses.
4. See the failure reproduced in the sandbox, a minimal patch verified by the gates, and a risk rating.
5. Approve with a signed token.
6. Get a PR carrying the report and a verifiable evidence bundle.

It also means non-code incidents end in a report only, with no patch.

Build order for the lean MVP:

| # | Increment | Notes |
|---|---|---|
| 1 | Queue and outbox (P0-09) | Lean: claim, lease, reaper, dedupe, outbox and NOTIFY |
| 2 | Reference app and fault injection | Small FastAPI shop and worker; 3 initial scenarios (S01 code defect, S21 configuration, S33 infrastructure) |
| 3 | Telemetry ingest, fingerprints, detector, incidents | Logs and metrics via OTLP; rules from D2 §6 |
| 4 | Code intelligence | git object reads, search, ast symbols, blame, suspect range |
| 5 | Investigation workflow | Finite state machine, LLM gateway (Groq/Gemini), prediction battery, citations, report |
| 6 | Sandbox broker, reproduction and verification gates | Hardened Docker; gVisor or cloud VM before hostile runs |
| 7 | Signed approval, evidence bundle, PR delivery, webhook | ADR-023 simplifications |
| 8 | Backup/restore commands; small benchmark | Harness over the scenario set |

## 1. MVP (Phases 0–5): "verified, approved proposal for a real team"

| Area | In the MVP |
|---|---|
| Platform | Repository, CI, migrations, authority matrix, roles, **RLS with a single active tenant**, per-tenant audit chains, queue, outbox, LISTEN/NOTIFY |
| Reference app | API, worker, payment stub, Postgres, Valkey; OTel; traffic generator; release tooling; fault injection; clean repository per trial |
| Telemetry | OTLP ingest, redaction, load shedding, fingerprints, windows, partitions, `TelemetrySource` port (built-in adapter), release webhook |
| Detection and incidents | Rules, baselines, hysteresis, incident manager through `incident_transition()`, suppression, severity, self-resolution, **webhook notifications** |
| Investigation | Codeintel (+ archive), frame mapper, pre-ranker, timeline, evidence bundles, **LLM gateway (Groq + second provider adapter, registry, egress policy, rate scheduler, parking)**, engine, all handlers, **prediction engine**, citation validator, consistency checks, routing to REPORT_ONLY and the escalations, run notes |
| Sandbox | Hardened runner profile, broker (async, attested, idempotent), trusted pytest plugin, escape suite, **Tier 2 gVisor before the injection scenarios run** (R-06), dedicated sandbox host |
| Reproduction and patch | Strategies S1–S4, signature rule (with broker recomputation), scope policy, AST scan, bounded retry, diagnosis, advisory patches (off by default) |
| Verification | Gates G01–G14, status function, baseline cache, broker-only writes |
| Risk and approval | Risk module, API recompute, JWS tokens (passkey claim reserved), separation of duties, re-authentication |
| Proof | **Proof bundle with investigation, verification and approval links**, `proof verify` CLI |
| UI | Incident list, live updates, run viewer, review UI (diff, regression test, matrix, risk, limits, proof), approve or reject |
| Replay | Strict and lenient replay; recorded-response tooling for CI (R-04) |
| Privacy | Two-layer redaction, data classes, provider registry, egress policy, retention per tenant, audit payload allow-list |
| Reliability | Crash-safe resume, graceful shutdown, startup reconciliation, degraded modes, nightly backup plus restore drill, dead-man heartbeat |
| Benchmark | Harness (live and recorded), hidden-test runner, 15 Wave 1 scenarios, baselines B0–B3, verification false-positive/negative rates, paired abstention, risk-coverage curve, pre-registration. **MVP results are labelled dev-split claims** (only 3 test scenarios). |

## 2. V1 (Phases 6–7): "safe recovery and learning"

| Area | In V1 |
|---|---|
| Delivery | **PR-only mode** (with post-deploy watch) and **managed mode**: branch, PR, CI, staging, health checks, canary 5/25/100, promotion approval, automatic rollback, delivery proof link (FR-15 in full) |
| Approval | **Passkey (WebAuthn) consent** verified by the deployer; credential cool-down; recovery runbook |
| Knowledge base | Case builder, closure form, **local embeddings**, hybrid retrieval, staleness flags, KB ablation |
| Benchmark | All 40 scenarios, ablations, live-mode run, human review, **public release of the open benchmark** (ADR-016) |
| Telemetry | Alertmanager intake; one external `TelemetrySource` adapter (Prometheus + Loki) |
| Tenancy | Multi-tenant admin (create, suspend, offboard), per-tenant quotas UI, erasure, per-tenant envelope encryption of blobs |
| Reliability | WAL point-in-time recovery, key escrow, key rotation drill, full runbooks |
| Security | Full security review, chaos run, audit read-logging |
| Docs | README, architecture, security, failure handling, runbooks, demo recordings, write-up |

## 3. Later (stretch, unchanged from D1 §14 plus new items)

Firecracker microVM tier · TypeScript language adapter · Kubernetes target and Helm · learned anomaly detection (must beat the rules) · learned patch-risk model · native Slack and PagerDuty apps · multi-service causal graph · **read-only MCP server for frozen incident bundles** · additional `TelemetrySource` adapters (Tempo, Sentry, Datadog) · silo deployment automation · data residency controls · native Anthropic adapter.

## 4. D1 tier reconciliation

| Item | D1 tier | Consolidated tier | Reason |
|---|---|---|---|
| Code-vs-operational routing | V1 (Capability D) | MVP | R-03: D2 builds it in Phase 3 |
| Run replay | V1 | MVP | R-04: CI needs it |
| Sandbox escape suite | V1 ("security test suite") | MVP | R-05 |
| gVisor tier | V1 | MVP (Phase 4) | R-06: required before injection scenarios |
| Everything else in D1 §14 | unchanged | unchanged | n/a |

These moves are all into **earlier** tiers (direction S).
