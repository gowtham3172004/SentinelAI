# 01. Consolidated architecture

Amends D2 §2–§4 and §7–§12. Anything not restated here still applies as written in D2.

## 1. Product statement and invariants

Sentinel AI is a **deployable production-reliability product**. A team installs it next to its own services. It detects incidents, investigates them against the deployed code, reproduces the failures, and prepares fixes that have been verified and approved by a human. Each fix comes with a proof chain that can be checked offline. When the evidence doesn't justify a code change, Sentinel declines to make one and explains why.

**System invariants.** Every one of these has at least one acceptance test (see `11-definition-of-done.md`).

| ID | Invariant | Enforced by |
|---|---|---|
| INV-1 | No claim reaches a report without a citation that resolves | Citation validator (AT-HYP-01, AT-RPT-01) |
| INV-2 | The model never sets an evidence level, gate result, risk level, verification status or prediction result | Pure domain functions; database authority (AT-LVL-01, AT-PRD-03, AT-RSK-02) |
| INV-3 | Model text never becomes a command | Fixed templates (AT-BRK-04) |
| INV-4 | A gate can be PASS only if an attested execution backs it | Constraint trigger plus attestation (AT-DB-05, AT-BRK-01) |
| INV-5 | Write authority is enforced by database roles; tenant isolation by RLS | Generated grants plus RLS (AT-DB-02, AT-TEN-01) |
| INV-6 | Nothing is deployed without human consent bound to the shipped bytes | Signed token plus passkey assertion verified by the deployer (AT-APR-01, AT-APR-04) |
| INV-7 | Every run ends in a terminal outcome with a report | Engine (AT-WF-02, AT-RPT-02) |
| INV-8 | Every side effect is idempotent and replayable | Idempotency keys plus recorded calls (AT-WF-03, AT-WF-06) |
| INV-9 | No tenant can read or affect another tenant's data, executions or quotas | RLS, labelling, quotas (AT-TEN-01 to 05) |
| INV-10 | No data leaves Sentinel for a provider that the tenant's data policy doesn't allow for that data class | Gateway egress policy (AT-LLM-05, AT-PRV-03) |
| INV-11 | Every link of a remediation is signed by the component that has authority over it, and the chain verifies offline | Proof bundles (AT-PRF-01 to 03) |

## 2. Logical architecture (consolidated)

```
┌──────── TARGET (per tenant; observed, never modified by Sentinel except via PR or managed deploy) ───────┐
│ services ─ OTLP ─► otel-collector        CI / deploy ─ HMAC ─► release webhook        Alertmanager (V1) │
└───────────────────────────────┬──────────────────────────────────┬──────────────────────────┬───────────┘
                                ▼                                  ▼                          ▼
                    ┌──────────────── sentinel-telemetry ────────────────────────────────────────┐
                    │ ingest (validate, redact, shed) · fingerprint · windows · detector         │
                    │ TelemetrySource port: builtin OTLP store (MVP) | external query (V1)       │
                    │ incident_transition() · release events · alert intake                      │
                    └───────────────┬─────────────────────────────────────────────────────────────┘
                                    │ jobs + outbox (same transaction)
          dashboard ◄─SSE── sentinel-api ◄─LISTEN/NOTIFY─ POSTGRES 16 + pgvector
          (Next.js)        (OIDC, tenancy, approvals,      (system of record · RLS · queue · outbox ·
                            risk recompute, JWS mint,       per-tenant audit chains · blobs index)
                            passkeys, notes, notifier)            ▲          ▲            ▲
                                    ┌───────────────────────────────┘          │            │
                    sentinel-orchestrator                                   blob store (fs | S3-compatible)
                    engine · LLM gateway (provider registry, egress policy,
                    rate scheduler) · prediction evaluator · reports · proof (investigation link)
                         │ tools              │ HTTPS (allow-listed)             │ 202 + execution.completed
                         ▼                    ▼                                  ▼
                 sentinel-codeintel     LLM providers                  sentinel-broker  ═══ SANDBOX HOST (Linux)
                 (mirrors per tenant,   Groq (primary) ·               exec queue · tar validator · templates ·
                  index, archive)       2nd provider (eval) ·          signature recompute · gates · attestation ·
                                        local (optional)               proof (verification link)
                                                                               └─► runners: network=none,
                                                                                   rootless, seccomp, AppArmor,
                                                                                   runsc (Tier 2)
          sentinel-api ── JWS + passkey assertion ──► sentinel-deployer ──► PR-only mode | managed mode
                                                      (proof: delivery link)            (staging → canary → rollback)
          harness (r_harness): live/recorded campaigns · hidden-test runner · scoring          [not reachable by Sentinel]
```

## 3. Processes and ownership (replaces D2 §3.1 where they differ)

### 3.1 Process table

| Process | Owns (writes) | Additions over D2 |
|---|---|---|
| sentinel-telemetry | telemetry tables, `incidents` (through functions), `incident_signals`, `incident_fingerprints`, `release_events`, `alerts_inbound` | Release webhook (B1), alert intake (V1), TelemetrySource port, load shedding |
| sentinel-api | `users`, `tenant_memberships`, `approvals`, `approval_tokens`, `webauthn_credentials`, `run_notes`, `notification_channels`, `noise_rules`, proof (approval link) | Risk recompute (C2), passkeys (C3), notes (D6), notification dispatch |
| sentinel-orchestrator | runs, steps, LLM and tool calls, hypotheses, **predictions**, evidence, reproductions (proposals), patches, risk (advisory), reports, KB, proof (investigation link) | Prediction evaluator (ADR-013), provider gateway (ADR-017), embeddings port (ADR-018) |
| sentinel-codeintel | `repo_revisions`, index files, mirrors | Role `r_codeintel` (R-02), archive endpoint (D2) |
| sentinel-broker | `execution_requests`, `sandbox_executions`, `verification_runs`, `verification_checks`, `verification_check_executions`, proof (verification link) | Async queue (D1), signature recompute (C1), trusted pytest plugin (C6) |
| sandbox runners | nothing | Tenant labels, per-tenant concurrency quotas |
| sentinel-deployer | `deliveries`, `health_checks`, `rollbacks`, proof (delivery link) | PR-only mode (ADR-021), passkey assertion verification, image provenance |
| sentinel-dashboard | none | Tenant switcher, passkey ceremonies, proof viewer |
| harness | `bench_*` tables, `bench_truth` schema | Separate role and runner (D3); never shares credentials with Sentinel |
| maintenance (cron entry point of telemetry) | partitions, retention deletes | Role `r_maint` |

### 3.2 Code layout

As in D2 §3.3 and §20.1, with the package root `src/sentinelai/` (OD-10): `sentinelai.domain`, `.services`, `.adapters`, `.apps.<process>`, `.cli`. **Pure functions that two different processes must compute independently** (signature match, gate status, risk score, evidence level, token claim checks, the prediction evaluator's pure core, proof statement canonicalisation) live in `sentinelai.domain`. That way the broker and the API recompute them rather than trusting the orchestrator.

### 3.3 Networks (replaces D2 §2.3)

| Network | Members | Notes |
|---|---|---|
| `net-target` | target services, collector | Per tenant in the reference setup |
| `net-telemetry` | collector, sentinel-telemetry | |
| `net-control` | api, orchestrator, telemetry, codeintel, **broker (control side)**, deployer (control side), postgres, blob store | Broker and deployer need the database (B2) |
| `net-egress-llm` | orchestrator only | Outbound HTTPS to allow-listed provider hosts only (by egress proxy or firewall rule) |
| sandbox host | broker data plane, runners | Runners use `--network=none`. Integration strategy S4 creates a **per-execution internal network** holding only that runner and trusted service containers. |
| `net-deploy` | deployer, registry, target staging and production, git host | The only network that can reach deployment targets |

## 4. Multi-tenancy (ADR-019)

**Model:** pooled by default (one deployment, shared database, RLS), with **silo** as an option (one deployment per tenant, same artefacts) for customers who need physical separation. The initial deployment runs a single tenant, `default`, **with every enforcement switched on**, so isolation is exercised from day one.

| Concern | Mechanism |
|---|---|
| Data | `tenant_id NOT NULL` on every tenant-scoped table. `ENABLE` + `FORCE ROW LEVEL SECURITY`. The policy compares against `current_tenant()`, which **raises if no tenant context is set** (fails closed). Every transaction runs `SET LOCAL sentinel.tenant_id`. |
| Queue and outbox | System-scoped tables. Payloads hold **only IDs plus tenant_id**, never tenant content. Workers set the tenant context from the claimed job before touching any data. |
| Identity | Global `users`. Roles held per tenant in `tenant_memberships`. Platform admin is a separate flag. |
| Audit | One hash chain **per tenant** plus a platform chain. This also removes the global lock contention (C4). |
| Secrets | Per tenant: webhook HMAC secrets, git and PR credentials (deployer), optional bring-your-own LLM keys. Stored as references to a secret store, never in rows. |
| Execution | Every execution and container is labelled with its tenant. One container per execution (unchanged). Concurrency and execution quotas per tenant. Workspaces never shared. |
| Code | Mirrors and indexes under `/<tenant_id>/` with per-tenant credentials |
| Knowledge base | Retrieval always scoped to the tenant. **No cross-tenant learning** unless a future ADR allows it. |
| Quotas | Per tenant: active runs, model tokens and requests per day, sandbox executions per day, storage (NFR-20) |
| Benchmark | Runs under a dedicated `bench` tenant |
| Bulk jobs | The detector tick iterates over tenants and sets context for each. Only `r_maint` (partitions and retention) has `BYPASSRLS`, and it has no read grants on content columns. |

## 5. Privacy architecture (FR-22)

**Data classes**

| Class | Examples | Rule |
|---|---|---|
| P0 public | Enum values, public config | No restriction |
| P1 internal | Metrics, counts, fingerprints, hashes | Stored; may be sent to any provider the tenant enabled |
| P2 confidential | Log text, traces, **source code**, diffs, reports | Stored redacted; sent only to providers the tenant's data policy allows for P2 |
| P3 secret or personal | Credentials, tokens, emails, card numbers | **Never stored raw, never sent anywhere**. Redacted at ingest (layer 1) and at egress (layer 2). |

**Controls**

1. A redaction pipeline at ingest and at gateway egress (D2 §5.3, §8.8). Detectors are versioned.
2. A **provider registry** records each provider's terms: `trains_on_inputs`, `retention`, `zero_retention_available`, `region`, `reviewed_at`. Each tenant has a `data_policy.egress` map: data class → allowed providers. **By default, real tenants deny any provider whose terms allow training on inputs, unless the tenant explicitly opts in.** The `bench` tenant holds only synthetic data and may use any provider.
3. Retention per tenant, within platform bounds (defaults are D2's A7). Evidence copies outlive dropped partitions, as in D2 §5.6.
4. **Erasure:** offboarding a tenant deletes all of its rows and blobs. Audit payloads may contain only IDs, hashes and enum values (enforced by a payload schema per action), so the chains still verify after erasure (AT-PRV-02).
5. Encryption at rest: disk encryption by the operator in the MVP; per-tenant envelope encryption of blobs, which allows crypto-shredding, in V1.
6. Local embeddings (ADR-018), so knowledge-base text never leaves the deployment for embedding.
7. Human reads of evidence and reports are audited (V1).

## 6. Provider independence (ADR-017, ADR-018)

### 6.1 LLM gateway

- **Port:** `LLMProvider.complete(request) -> ProviderResponse`.
- **Adapters:** `openai_compatible` (one adapter for Groq, Gemini's OpenAI-compatible endpoint, OpenRouter, Cerebras, Ollama, vLLM and LM Studio) in the MVP. A native `anthropic` adapter is optional later.
- **No vendor SDKs.** Calls go through `httpx`, which gives full control of headers and rate-limit data.
- **Model registry** (configuration): `id, provider, family, context_window, max_output, capabilities{json_schema, json_object, tools}, limits{rpm, tpm, rpd, tpd}, price{in, out}, data_terms_ref`. A startup **capability probe** checks that configured models still exist (Groq retires models often) and marks missing ones unavailable.
- **Routing:** purpose → tier → an ordered list of candidate models. **On the first call for a purpose, the run pins that model.** Failover to another candidate happens only on unavailability. It's recorded (`failover_from`) and reported as a reproducibility limitation.
- **Structured output strategy per capability:** `json_schema` (strict) > `tools` > `json_object` + Pydantic validation. Then one repair attempt (unchanged from D2).
- **Rate-limit-aware scheduler:** token buckets per (provider, model, key), fed from configuration and refreshed from response headers (`x-ratelimit-*`, `retry-after`). Free tiers impose daily caps, so a run that would exceed a limit **parks**: the job is deferred, and parked time doesn't count against the wall-clock budget, up to `max_park` (initially 6 h). After that the run ends `ESCALATED_DEPENDENCY_UNAVAILABLE` (R-01).
- **Provider-aware context budgets:** pack budget = min(purpose budget from D2 §8.9, context window − max output − margin, the largest single request the model's TPM allows). Sections dropped to fit are recorded as limitations, as in D2.
- **Egress policy check** on every call (INV-10). The decision is recorded on the call.

### 6.2 Initial model plan

Groq free tier for the MVP, verified in spike P0-12. Model names change, so these are **candidates, not commitments**:

- **Fast tier:** a small instant model (for example `llama-3.1-8b-instant`).
- **Strong tier:** the strongest available model with JSON-schema support (for example `openai/gpt-oss-120b`, `llama-3.3-70b-versatile`, `moonshotai/kimi-k2-instruct`, `qwen/qwen3-32b`).

Spike P0-12 produces the actual capability and limit matrix.

### 6.3 Independent evaluation provider

The judge model, and optionally a second system-under-test configuration, must come from a **different model family** from the strong tier, and preferably from a different provider. The harness validates this at campaign start. Candidates: Google Gemini API free tier, Cerebras or OpenRouter free models, or a local Ollama model if the hardware allows. The decision is left open until spike P0-12 (open issue OI-03).

### 6.4 Embeddings

- **Port:** `Embedder.embed(texts) -> vectors`, with `model_id` and `dims`.
- **Default adapter:** local, using `fastembed` (ONNX, CPU) with `BAAI/bge-small-en-v1.5` (384 dimensions, MIT).
- **External adapters** go behind the same port.
- Vectors are stored with their model ID. Changing the model triggers a re-embedding job. One HNSW index per model.

## 7. Delivery modes (ADR-021)

| Mode | Flow | When |
|---|---|---|
| **PR-only** (default for adopters) | Verify token and passkey → branch → PR (report plus proof bundle attached) → record CI → **post-deploy watch**: when a release event arrives whose commit contains the approved patch, the detector watches the fingerprint for the monitoring window. If it's absent: resolve. If it recurs: reopen and notify, with rollback *recommended*. | The tenant's own CD owns production |
| **Managed** | D2 §11.3 in full: staging → promotion approval → canary 5/25/100 → monitoring → automatic rollback | Reference environment and tenants who delegate deployment. **FR-15 is fully implemented here.** |

**Provenance rules (B8)**

- The patch from merge-base to PR head must equal the approved `patch_sha256`.
- If the base moved, the patch is rebased, verified again and approved again.
- Every deployed image digest is recorded together with its source commit, and the delivery proof link binds `image_digest` to `merged_sha`.

## 8. Telemetry sources and incident triggers (FR-32)

- **Port:** `TelemetrySource`, with query methods for logs, metrics, traces and releases scoped to a time range.
- **MVP:** the built-in OTLP store (D2 §5).
- **V1:** one external adapter (Prometheus + Loki) and alert intake (an Alertmanager webhook opens or attaches incidents through the same incident manager).
- Evidence bundles freeze whatever any source returns, so the investigation logic doesn't depend on where the data came from.

## 9. End-to-end flows (replaces D2 §4.1 where they differ)

| ID | Flow | Change from D2 |
|---|---|---|
| F1 | Telemetry | Batched `INSERT … unnest` (R-10). Shedding under burst keeps 100% of error records. |
| F2 | Release events | Endpoint on sentinel-telemetry; idempotency key from the sender |
| F3 | Detection | Per-tenant tick |
| F4 | Investigation | Parks on provider quota; notes ingested at state boundaries |
| F5 | Execution | **Async**: `POST` → 202 + request ID → broker queue → `execution.completed` event (ADR-020) |
| F6 | Review | Risk recompute; JWS (MVP) plus passkey assertion (V1) |
| F7 | Delivery | Mode-dependent (§7) |
| F8 | Learning | Local embeddings |
| F9 | Live UI | Outbox → LISTEN/NOTIFY → API SSE (ADR-022) |
| F10 | Notification | Outbox → signed webhook (Slack-compatible payload option), retried at least once |
| F11 | Proof | Each authority appends its signed link: investigation (orchestrator), verification (broker), approval (API), delivery (deployer) |
| F12 | Run notes | Dashboard → API → `run_notes` → orchestrator appends a `human_note` evidence item at the next state boundary |
| F13 | Recorded mode | Raw OTLP files → ingest (the same path as live) |
