# 09. Scope: MVP, V1 and later

Supersedes the tier lists in D1 §7 and §14 where they differ. **Every original item is listed below. Nothing is dropped.** The D1 §14 cut line still applies, unchanged: *never* drop the sandbox security model, signed approvals, the failure-signature rule or the abstention scenarios. The following are added to that protected list: tenant isolation enforcement, the egress policy, and broker-side signature recomputation.

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
