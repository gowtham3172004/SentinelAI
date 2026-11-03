# 12. Requirements traceability matrix

Every original requirement (D1 goals, non-goals, principles, metrics, abstention rules and capability done-criteria; D2 functional and non-functional requirements), plus every new one, maps to a **component**, a **phase** and the **acceptance tests** that prove it.

**Status values:**
- **Kept**: as in the original.
- **Strengthened**: the original stays, plus more.
- **Clarified**: wording resolved with equivalent or stronger guarantees.

None are weakened.

## 1. D2 functional requirements

| Req | Summary | Status | Component(s) | Phase | Acceptance tests |
|---|---|---|---|---|---|
| FR-01 | Ingest OTLP + authenticated deploy events | Clarified (webhook on telemetry, B1) | telemetry.ingest, telemetry.releases | 2 | AT-ING-01/04, AT-REL-EV-01 |
| FR-02 | Normalise exceptions, stable fingerprints | Kept | domain.fingerprint, telemetry.ingest | 2 | AT-FP-01/02/03 |
| FR-03 | Deterministic detection, duplicate and noise suppression | Kept | telemetry.detector | 2 | AT-DET-01/02/03, AT-INC-04 |
| FR-04 | Incident lifecycle, severity, append-only history | Strengthened (`incident_transition()`, self-resolution) | telemetry.incidents, db | 2 | AT-INC-01/02/03/05, AT-AUD-04 |
| FR-05 | Frozen evidence bundle, pinned revision | Kept | orchestrator.evidence | 3 | AT-EV-01/02 |
| FR-06 | Repository index at a revision; search, symbol, blame, diff, tests | Strengthened (archive endpoint) | codeintel | 3 | AT-CI-01/02/03/04/05 |
| FR-07 | Classify and route incidents | Kept (MVP, R-03) | orchestrator.handlers.triage/validate | 3 | AT-TRI-01/02, AT-WF-01 |
| FR-08 | Competing hypotheses with validated citations | Strengthened (prediction engine) | orchestrator.hypotheses, orchestrator.predictions | 3 | AT-HYP-01/02/03, AT-PRD-01…04 |
| FR-09 | Sandbox reproduction with signature rule | Strengthened (broker recompute, plugin) | orchestrator.reproduce, broker | 4 | AT-REP-01…05, AT-BRK-06/07 |
| FR-10 | Patches within scope policy, bounded retries | Kept | orchestrator.patch | 4 | AT-PAT-01…04 |
| FR-11 | Gate verification; deterministic status; broker-only writes | Strengthened (multi-execution links) | broker.verification | 5 | AT-VER-01…05, AT-DB-05 |
| FR-12 | Rule-based risk assessment | Strengthened (API recompute) | domain.risk, api | 5 | AT-RSK-01/02 |
| FR-13 | Report rendered from structured records | Kept | orchestrator.report | 3, 5 | AT-RPT-01/02 |
| FR-14 | Signed human approval bound to hashes; second approver for HIGH | Strengthened (passkeys V1; minimum configurable upward only) | api.approvals, deployer | 5, 6 | AT-APR-01…05 |
| FR-15 | Branch, PR, CI, staging, health, canary, rollback | Kept in full (managed mode) + PR-only addition | deployer | 6 | AT-DLV-01…07 |
| FR-16 | Closed incidents become cases; advisory retrieval | Kept (local embeddings) | kb | 7 | AT-KB-01…04 |
| FR-17 | Tamper-evident audit log | Strengthened (timezone-safe, per-tenant chains) | db.audit | 0+ | AT-AUD-01…04 |
| FR-18 | Dashboard | Kept | dashboard | 2+ | AT-UI-01/02/03 |
| FR-19 | Evaluation harness with baselines and ablations | Strengthened (clean repo per trial, hidden-test runner) | harness | 3+ | AT-EVAL-01…06 |
| FR-20 | Replay from recorded outputs | Kept (MVP, R-04) | cli.replay, orchestrator.engine | 5 | AT-WF-06 |

## 2. New functional requirements

| Req | Requirement | Source | Component(s) | Phase | Acceptance tests |
|---|---|---|---|---|---|
| FR-21 | Tenant isolation of data, execution, secrets, quotas and the KB | OD-12, ADR-019 | db (RLS), all services, broker | 0+ | AT-TEN-01…05 |
| FR-22 | Privacy: data classes, two-layer redaction, provider egress policy, retention per tenant, erasure | OD-12 | telemetry.ingest, orchestrator.gateway, maintenance | 0–7 | AT-ING-03/05, AT-LLM-04/05, AT-PRV-01…04 |
| FR-23 | Provider-independent LLM gateway: registry, capability probe, rate-aware scheduling, parking, failover | OD-01, ADR-017 | orchestrator.gateway | 3 | AT-LLM-01…09 |
| FR-24 | Embedding abstraction with a local default | OD-02, ADR-018 | adapters.embeddings | 3 | AT-EMB-01 |
| FR-25 | Independent evaluation provider (different model family) | OD-03 | harness, gateway | 3, 7 | AT-EVAL-04, AT-LLM-07 |
| FR-26 | Prediction engine | OD-07, ADR-013 | orchestrator.predictions, domain | 3 | AT-PRD-01…04 |
| FR-27 | Cryptographic proof bundles | OD-07, ADR-014 | orchestrator, broker, api, deployer, cli.proof | 5, 6 | AT-PRF-01/02/03 |
| FR-28 | Passkey-based human approval | OD-07, ADR-015 | api.approvals, deployer, dashboard | 6 | AT-APR-04 |
| FR-29 | Open benchmark | OD-07, ADR-016 | bench, harness | 5, 7 | AT-EVAL-*, B57a |
| FR-30 | Outbound notifications | Finding D5 | api.notifier | 2 | AT-NOT-01 |
| FR-31 | Delivery modes: PR-only and managed | E2, ADR-021 | deployer | 6 | AT-DLV-01/07 |
| FR-32 | `TelemetrySource` port; alert intake; external adapters | E1 | telemetry | 2 (port), 7 (adapters) | Import-linter contract; V1 adapter tests |
| FR-33 | Human notes on runs | D6 | api, orchestrator | 3 | AT-WF-08 |

## 3. Non-functional requirements

| Req | Summary | Status | Component(s) | Acceptance tests |
|---|---|---|---|---|
| NFR-01 | Incident within 90 s | Kept | detector | AT-DET-02 |
| NFR-02 | Crash-resume without duplicates | Kept | engine, queue, broker, deployer | AT-WF-03, AT-BRK-03, AT-DLV-06, AT-OPS-05 |
| NFR-03 | Sandbox: no secrets, no network, zero escapes | Strengthened (Tier 2 in the MVP, dedicated host) | broker, sandbox | AT-SBX-01…15 |
| NFR-04 | 100% auditability; chain verifies | Strengthened | db.audit, all services | AT-AUD-01…04 |
| NFR-05 | Hard budgets | Strengthened (tokens, requests, tenant quotas) | engine, gateway | AT-WF-04, AT-LLM-02, AT-TEN-05 |
| NFR-06 | Pinned model, prompt and image versions per run | Strengthened (model pinning, failover recorded) | engine, gateway | AT-LLM-03/09, AT-EVAL-05 |
| NFR-07 | ≥ 500 events/s | Kept (NFR-16 adds 1,000/s) | ingest | AT-ING-01 |
| NFR-08 | Redaction before model calls | Strengthened (egress policy by data class) | ingest, gateway | AT-ING-03, AT-LLM-04/05 |
| NFR-09 | Testable without a model | Kept | domain, recorded responses | AT-WF-06, import-linter contracts |
| NFR-10 | One-command start; health endpoints | Kept | ops | AT-OPS-08 |
| NFR-11 | Backup and restore RPO/RTO | New | ops | AT-OPS-01 |
| NFR-12 | Degraded modes | New | all | AT-OPS-04/05, AT-LLM-06 |
| NFR-13 | Graceful shutdown and reconciliation | New | all | AT-OPS-02/03 |
| NFR-14 | Safe upgrades | New | db, engine | AT-DB-01, AT-WF-07 |
| NFR-15 | Monitoring independent of Sentinel | New | ops | AT-OPS-06 |
| NFR-16 | Burst ingest preserving errors | New | ingest | AT-ING-01/02 |
| NFR-17 | Zero cross-tenant access | New | db, services | AT-TEN-01…03 |
| NFR-18 | Licence compliance | New | CI | AT-LIC-01 |
| NFR-19 | Key rotation without downtime | New | api, broker, orchestrator, deployer | AT-OPS-07 |
| NFR-20 | Per-tenant quota fairness | New | gateway, broker, engine | AT-TEN-05 |

## 4. D1 goals (G) and non-goals (N)

| ID | Summary | Component(s) | Acceptance tests |
|---|---|---|---|
| G1 | Measurable deterministic detection | detector, harness | AT-DET-01/02 |
| G2 | Evidence-cited investigation | orchestrator | AT-HYP-*, AT-PRD-*, AT-RPT-01 |
| G3 | Reproduce with signature proof | orchestrator, broker | AT-REP-*, AT-BRK-06 |
| G4 | Minimal patches, really executed verification | orchestrator, broker | AT-PAT-*, AT-VER-* |
| G5 | Route and abstain correctly | orchestrator | AT-TRI-01, AT-WF-01, campaign abstention pair |
| G6 | Humans in control; staged deploy; rollback; audit | api, deployer, db | AT-APR-*, AT-DLV-*, AT-AUD-* |
| G7 | Learn as evidence, never authority | kb | AT-KB-02 |
| G8 | Measurable: benchmark, baselines, ablations | harness | AT-EVAL-* |
| N1 | Not a fully autonomous production changer | deployer, api | AT-APR-01 (no deploy without a token); Sentinel never merges (AT-DLV-02) |
| N2 | No model training | n/a | Design constraint; no training code |
| N3 | No unrestricted production access | deployer credentials, TB-8 | AT-DLV-01 (prefix-limited credentials), 04 §3 |
| N4 | Not a general observability platform | telemetry | Retention defaults (AT-PRV-01); `TelemetrySource` lets adopters keep their own stack |
| N5 | Python only (MVP and V1) | LanguageAdapter | Design constraint |
| N6 | No automatic migrations or data repair | scope policy, G14 | AT-PAT-01 (migration paths forbidden) |
| N7 | Never claims "production-ready" | report | AT-RPT-01 (fixed disclaimers present) |
| N8 | No Kubernetes in MVP/V1 | ops | Design constraint |

## 5. D1 principles → invariants

| D1 principle | Invariants / tests |
|---|---|
| 1. Evidence over assertion | INV-1 · AT-HYP-01, AT-RPT-01 |
| 2. Evidence ladder, not confidence | INV-2 · AT-LVL-01, AT-PRD-03 |
| 3. The model proposes, the system disposes | INV-2, INV-3 · AT-BRK-04, AT-VER-05 |
| 4. Humans hold the final key | INV-6 · AT-APR-01/04 |
| 5. Know when to stop | INV-7 · AT-WF-02/04, AT-TRI-01 |
| 6. Every input hostile | AT-EV-02, AT-SEC-02, AT-SBX-* |
| 7. Auditable and replayable | INV-8 · AT-AUD-*, AT-WF-06 |
| 8. Measure, don't demo | AT-EVAL-*, MVP DoD §3.3 |
| 9. Small and reliable | Scope 09; cut line |

## 6. D1 abstention rules

| Rule | Mechanism | Acceptance tests |
|---|---|---|
| 1. No app frame and no release correlation | Triage priors, system battery | AT-TRI-01, AT-PRD-03 |
| 2. All hypotheses refuted after the allowed revisions | VALIDATE transitions | AT-WF-01 |
| 3. Not reproduced | REPRODUCE transitions; advisory off by default | AT-REP-04, AT-PAT-04 |
| 4. Patch exceeds scope | PATCH → ESCALATED_POLICY | AT-PAT-01 |
| 5. Budget exhausted | Engine budgets | AT-WF-04 |
| 6. Policy violation | Tool policy | AT-WF-05 |

## 7. D1 capability done-criteria and success metrics

| Item | Target (D1) | Measured by |
|---|---|---|
| Capability A: localisation top-3 | ≥ 85% | AT-CI-06 + campaign metric |
| Capability B: reproduction rate, signature-gated | ≥ 60%; none without a match | Campaign metric + AT-REP-04 |
| Capability C: no PASS without an attested execution | Hard rule | AT-DB-05, AT-BRK-01 |
| Capability D: staging pipeline, rollback, audit | Demonstrated | AT-DLV-03/05, AT-AUD-04 |
| Capability E: advisory-only KB, stale flags, ablation | Demonstrated | AT-KB-02/03, B56 ablation |
| Detection precision / recall | ≥ 0.90 / ≥ 0.95 | AT-DET-01 |
| Root-cause accuracy | ≥ 0.70 on test, +15 points over B1 | Campaign (D2 §18.6) + AT-EVAL-04 judge calibration |
| Patch success | ≥ 50% | Campaign, hidden tests |
| Regression rate | ≤ 5% | Campaign, hidden tests |
| Safe abstention | ≥ 85% correct, ≤ 25% false | Campaign pair metric |
| Unsafe-action rate | 0 | AT-SEC-02, S35–S37 |
| Time to diagnosis | median ≤ 10 min | Campaign (parked time reported separately, RK-01) |
| Cost per incident | median ≤ 2 USD, p90 ≤ 5 USD | Campaign; on free tiers, tokens and requests reported alongside USD |
| Human acceptance | ≥ 60% | Phase 7 blind review |

Campaign metrics are reported as measured, with confidence intervals (D2 §18.7). Missing a target is reported, never hidden.
