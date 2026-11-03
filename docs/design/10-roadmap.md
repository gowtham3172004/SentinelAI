# 10. Implementation sequence, dependencies, risk register and open issues

Amends D2 §21. It also supplies D2's missing "Section 22" (the risk register and open issues).

## 1. Ordering principles

D2 §21.1 still applies. Added:

7. **Prove the hardest infrastructure assumptions in week one.** Two things could invalidate the plan: the sandbox host and free-tier model feasibility. Both are spiked in Phase 0.
8. **Every component ships with its acceptance tests** (`11-definition-of-done.md`). A step is done when its AT IDs pass in CI.
9. **Small increments.** One pull request per build step. No step lands without tests.

## 2. Pace (OD-05)

D2's hour estimates assumed a single developer working without AI help. Implementation now happens with AI pairing, so the bottlenecks move to:
- your review time,
- sandbox host setup,
- free-tier quotas,
- and validating scenarios.

**Estimates are re-baselined at the Phase 0 gate**, using the measured pace of P0-01 to P0-16. Speed comes from:
- parallel tracks (below);
- recorded responses, so model quotas don't block development;
- the walking skeleton first, then widening.

Safety work isn't compressed (D2 §21.5).

**Parallel tracks after Phase 0**

- **Track P (platform):** telemetry → detection → engine.
- **Track R (reference app and scenarios):** starts in Phase 1 and continues as the Wave 2 stream.
- **Track S (sandbox):** the broker starts in Phase 3, not Phase 4. It depends only on B03 and B32, so moving it earlier takes the highest-risk work off the end of the critical path.

## 3. Build steps

D2's step IDs are kept. New steps carry a letter suffix. The **Moved** column marks steps that change phase.

### Phase 0: Foundations, walking skeleton, feasibility spikes (detailed plan: `13-phase-0-plan.md`)

| Step | Component | Depends on | Acceptance |
|---|---|---|---|
| B00 | Repository, packaging, tooling, CI | none | AT-SEC-01 (static stage), CI green |
| B01 | Compose skeleton (postgres + pgvector, collector) | B00 | AT-OPS-08 (partial) |
| B02 | Domain enums and types | B00 | Schema snapshot tests |
| B03 | Migration 1 + roles + triggers | B01, B02 | AT-DB-01/03 |
| **B03a** | Authority matrix, grant generator, RLS, `current_tenant()`, per-tenant audit chain | B03 | AT-DB-02, AT-TEN-01/02, AT-AUD-01/02/03 |
| B04 | ADRs 001–022, threat model v0, scenario spec format, pre-registration stub | B00 | Documents committed |
| B05 | Queue (claim, lease, heartbeat, reaper, dedupe) + outbox | B03 | AT-Q-01 to 04 |
| **B05a** | LISTEN/NOTIFY fan-out + graceful shutdown primitives | B05 | AT-OPS-02 (queue part) |
| **S-SBX** | WSL2 sandbox feasibility spike | none | SB-1 to SB-8 report + decision (04 §5) |
| **S-LLM** | Groq / second provider / local embedding feasibility spike | none | Capability and limit matrix; quota model; decision on OI-01/03 |
| **B00a** | Licence review tooling, LICENSE, NOTICE | B00 | AT-LIC-01 |

### Phase 1: Reference app and incident data

Steps B06–B12 as in D2, plus:
- **B11a:** clean repository per trial, plus raw OTLP capture for recorded mode (B6, C7). Acceptance: AT-EVAL-03.

### Phase 2: Telemetry, detection, incidents

Steps B13–B20 as in D2, with these modifications:

| Step | Change |
|---|---|
| B13 | Uses `unnest` batching (R-10) and adds shedding. Acceptance: AT-ING-01/02. |
| B16 | Release webhook on telemetry (B1), with an idempotency key |
| B18 | Uses `incident_transition()` (B3), adds self-resolution |
| B20 | Tenant-scoped API, OIDC through Keycloak |

New steps:

| Step | Component | Acceptance |
|---|---|---|
| **B16a** | `TelemetrySource` port and built-in adapter | Investigation code depends only on the port (import-linter contract) |
| **B18a** | Webhook notifier (FR-30) | AT-NOT-01 |
| **B20a** | Blob store adapter, nightly backup + restore drill, dead-man heartbeat | AT-OPS-01, AT-OPS-06 |

### Phase 3: Code intelligence, gateway, engine, read-only investigation

Steps B21–B31 as in D2, plus:

| Step | Component | Acceptance |
|---|---|---|
| **B21a** | Harness role, hidden-test runner | AT-EVAL-02 |
| **B22a** | Codeintel archive endpoint | AT-CI-04 |
| **B25a** | Provider registry, capability probe, rate scheduler, parking, egress policy, provider-aware budgets, second-provider adapter contract tests | AT-LLM-04 to 08 |
| **B25b** | Embedder port + fastembed adapter | AT-EMB-01 |
| **B28a** | Prediction engine: query AST, evaluator, battery, matrix, levels | AT-PRD-01 to 04 |
| **B29a** | Run notes | AT-WF (notes ingestion) |
| B32, B33 **(Moved: start late in Phase 3, Track S)** | Sandbox base image, broker | as in Phase 4 |

### Phase 4: Sandbox, reproduction, patching

Steps B32–B40 as in D2, plus:

| Step | Component | Acceptance |
|---|---|---|
| **B33a** | Async execution queue (`execution_requests`) | AT-BRK-03 |
| **B33b** | Trusted `sentinel-pytest` plugin + cross-checks | AT-BRK-07 |
| **B55 (Moved from Phase 7, R-06)** | gVisor tier on the dedicated sandbox host. **Must pass before S35 runs.** | AT-SBX-15 |

### Phase 5: Verification, approval, proof (MVP)

Steps B41–B47 as in D2, plus:

| Step | Component | Acceptance |
|---|---|---|
| **B41a** | Broker-side signature recompute for G01/G03 | AT-BRK-06 |
| **B43a** | Proof bundle: investigation, verification and approval links; `proof verify` | AT-PRF-01 to 03 |
| **B44a** | API risk recompute; JWS with `ten`/`vrs`; HIGH ≥ 2 configurable upward only | AT-RSK-02, AT-APR-01/02 |

**Exit: MVP definition of done (11 §3).**

### Phase 6: Controlled delivery

Steps B48–B53 as in D2, plus:

| Step | Component | Acceptance |
|---|---|---|
| **B48a** | PR-only mode and post-deploy watch, built **before** managed mode | AT-DLV-01, AT-DLV-07 |
| **B51a** | Passkeys: registration, consent, deployer verification, cool-down | AT-APR-04 |
| **B52a** | Delivery proof link + image provenance | AT-PRF-02 (delivery link) |

### Phase 7: Learning, evaluation, hardening, publication

| Step | Change |
|---|---|
| B54 | Knowledge base with local embeddings |
| B56, B57, B58 | As in D2 |
| B59 | As in D2 + tenancy and privacy review |
| B60 | As in D2 + runbooks |
| B61 | As in D2 |

New steps:

| Step | Component | Acceptance |
|---|---|---|
| **B57a** | Open benchmark packaging: spec, licence (code Apache-2.0; scenario data CC-BY-4.0, proposed), submission format, leaderboard script | ADR-016 acceptance |
| **B60a** | V1 reliability: point-in-time recovery, key escrow, rotation drill | AT-OPS-07, RPO measured |
| **B60b** | Multi-tenant admin, erasure, envelope encryption, Alertmanager intake, Prometheus + Loki adapter | AT-PRV-02, AT-TEN-05 |

## 4. Critical path

**Updated path:** B03 → B03a → B05 → B18 → B26 → B28 → B28a → B35 → B36 → B41 → B41a → B44 → B44a → *(MVP)* → B48 → B48a → B52 → B58.

The sandbox chain (S-SBX → B32 → B33 → B34 → B55) now runs **in parallel**, from Phase 0 through Phase 4, and must be finished before B35. If S-SBX fails the WSL2 criteria, the cloud VM becomes a Phase 0 deliverable. It's on the critical path only if it's late.

## 5. Phase gates

D2 §21.7 still applies: record exit-criteria results, tag `v0.<phase>`, archive results and recorded responses, review the threat model, write a retrospective, re-estimate, apply the cut line if needed. **Added:** traceability matrix updated (12), and the AT pass list attached to the tag.

## 6. Risk register (D2's missing §22)

| ID | Risk | Likelihood | Impact | Mitigation | Owner trigger |
|---|---|---|---|---|---|
| RK-01 | Free-tier quotas too small for investigations or campaigns | High | High | Parking, multiple providers, recorded responses, multi-day campaigns, local inference option, measure in S-LLM and Phase 3 | Phase 3 gate: tokens per run measured |
| RK-02 | WSL2 can't meet sandbox guarantees | Medium | Medium | Cloud VM fallback decided in S-SBX | P0 gate |
| RK-03 | Open models weaker at patching than frontier models | High | Medium | Strong-tier selection by measurement; honest reporting; the architecture is model-independent | Phase 4 B40 |
| RK-04 | Groq retires or renames models | High | Low | Registry, capability probe, equivalence classes | Continuous |
| RK-05 | RLS overhead on telemetry hot paths | Low | Medium | `tenant_id`-leading indexes; AT-ING-01 measured with RLS on | Phase 2 |
| RK-06 | Licence conflict (Semgrep rules, LGPL handling) | Medium | Medium | P0-13 review; Opengrep or own rules | P0 gate |
| RK-07 | Review bottleneck (one human) | High | Medium | Small PRs, AT-driven acceptance, automated checks | Each phase |
| RK-08 | Passkey lockout | Low | Medium | Two-admin recovery runbook RB-07 | Phase 6 |
| RK-09 | Benchmark bias (author writes both faults and fixes) | Medium | High | Mutation-operator faults, external scenario set, separate reporting (D2 §18.8) | Phase 7 |
| RK-10 | Prediction engine raises false abstention | Medium | Medium | Battery tuned on dev only; paired metrics; ADR to adjust | Phase 3 |
| RK-11 | Scope and schedule creep | Medium | High | Cut line (D1 §14, extended in 09) | Each gate |
| RK-12 | Provider data terms unacceptable for real tenants | Medium | High | Egress policy defaults to deny; local or BYO-key providers | Before the first real tenant |

## 7. Open issues (decided at the named gate)

| ID | Issue | Decided at |
|---|---|---|
| OI-01 | Whether a small paid budget is needed for campaigns. Local GPU inference is ruled out (OD-14: no GPU), so campaign capacity depends on free API tiers. | Phase 3 gate (after measurement) |
| OI-02 | Groq strong-tier model choice | P0 gate (S-LLM) |
| OI-03 | Second provider for independent evaluation | P0 gate (S-LLM) |
| OI-04 | WSL2 vs cloud VM for hostile workloads | P0 gate (S-SBX) |
| OI-05 | Weighted proxy (Traefik vs nginx) | Phase 6 |
| OI-06 | Project domain or repository URL for `predicateType` URIs and the public benchmark | Before Phase 5 |
| OI-07 | Move the repository to the WSL filesystem | P0-01 |
| OI-08 | Licence for benchmark scenario data (CC-BY-4.0 proposed) | Phase 7 |
| OI-09 | Blind reviewers for human acceptance (D1 Q5) | Phase 6 |
