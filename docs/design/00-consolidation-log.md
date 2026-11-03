# 00. Consolidation log

Version 1.1-draft · 2026-10-31 · Status: **for review**

## 0.1 Precedence

1. The two original documents (`Sentinel AI Project Document.md`, called **D1**, and `Sentinel AI Technical Design Document and Roadmap.md`, called **D2**) are the **frozen baseline**. They stay in the repository unchanged.
2. The files in `docs/design/` and `docs/adr/` **amend** the baseline. Where they conflict with D1 or D2, the amendment wins, **and the conflict is listed in this log**.
3. Anything in D1 or D2 that is not mentioned here **still applies as written**.
4. No requirement is removed or weakened. Every change below is labelled with its direction:
   - **S**: strengthens a requirement.
   - **C**: clarifies or resolves a contradiction, with equivalent or stronger guarantees.
   - **A**: an addition.

   A change labelled **W** (weakens) would need an explicit owner decision. **There are none.**

## 0.2 Owner decisions taken (2026-10-31)

| # | Decision | Where it lands |
|---|---|---|
| OD-01 | Start with free Groq models behind a provider-independent LLM gateway | ADR-017, 01 §6, 08 |
| OD-02 | Local embeddings by default, behind an abstraction that allows external providers | ADR-018, 01 §6.4 |
| OD-03 | A second model provider for independent evaluation | ADR-017, 01 §6.3 |
| OD-04 | A dedicated Linux sandbox. A WSL2 Ubuntu + Docker Engine spike first; a cloud Linux VM if WSL2 can't meet the guarantees | 04 §5, 13 (P0-11) |
| OD-05 | Finish as fast as possible, without dropping safety | 10 §2 |
| OD-06 | A real, deployable product, not a portfolio demo | 01 §1, 09 |
| OD-07 | Adopt all four additions: the prediction engine, cryptographic proof bundles, passkey approval and the open benchmark | ADR-013 to ADR-016 |
| OD-08 | Apache-2.0 licence, subject to a review of dependency licences | 08 §3, P0-13 |
| OD-09 | Next.js dashboard | unchanged from D2 |
| OD-10 | The Python package stays `sentinelai` | 01 §3 (code layout uses `sentinelai.*` where D2 says `sentinel.*`) |
| OD-11 | Rename `master` to `main` | Done 2026-10-31 |
| OD-12 | Tenant isolation and privacy designed in from the start; the initial deployment stays manageable | ADR-019, 01 §4–5, 02 §2 |
| OD-13 | Explicit failure-recovery and reliability requirements for Sentinel itself | 07 |
| OD-14 (2026-10-31) | **No GPU available.** Local inference is CPU-only with small models (fast-tier experiments). Strong-tier and judge calls rely on free API tiers. | 08 §2, 10 §7 OI-01 |
| OD-15 (2026-10-31) | **Terminal-first and cross-platform.** The `sentinelai` CLI runs natively on Windows, Linux and macOS for everything except sandboxed execution, which needs a Linux host (04 §5). The P0-12 provider probe is built as product code (`sentinelai providers probe`) instead of a throwaway spike. Dev tasks run through `uv run …` (cross-platform) instead of a Makefile. | 13 §5 |

## 0.3 Review findings and their disposition

Finding IDs refer to the architecture review of 2026-10-31.

| ID | Finding | Dir | Resolution |
|---|---|---|---|
| A1 | D2 refers to Section 22, Section 23 and Appendices A–C, none of which exist; the MVP has no definition of done | A | 10 §6 (risk register and open issues), 11 (definition of done), 06 (Appendices A–C) |
| B1 | Release webhook placed on sentinel-api, but sentinel-telemetry owns the table | C | Served by sentinel-telemetry. 05 §2 |
| B2 | The broker can't reach the database; runners don't actually sit on `net-sandbox` | C | The broker joins net-control. `net-sandbox` becomes per-execution private networks. 01 §3.3 |
| B3 | Orchestrator and API must write incidents but their roles can't | C | `incident_transition()` and `incident_annotate()`, both SECURITY DEFINER. 02 §4 |
| B4 | Patch statuses written by roles that lack the authority | C | Stored statuses reduced; derived `patch_lifecycle` view. 02 §4 |
| B5 | NFR-07 (500/s) is below the A3 burst (≈833/s) | S | NFR-16: 1,000/s sustained, errors preserved under shedding. NFR-07 kept. |
| B6 | Recorded snapshots required before ingest exists | C | The snapshot is raw OTLP from the collector's file exporter. 10 §3 |
| B7 | S38 scoring conflicts with the S3 strategy | C | False reproduction redefined. 11 AT-REP-04 |
| B8 | Merged tree vs approved diff; image provenance | C | Merge-base-to-head patch must equal the approved diff; image digest bound to commit. 01 §7 |
| B9 | No path for an incident that resolves itself | A | `→ resolved (self)` transition; the run continues and is flagged. 03 §2 |
| B10 | `release_events` unique key blocks repeated config changes | C | Sender idempotency key. 02 §4 |
| B11 | Per-step estimates unrealistic | C | Re-baselined after Phase 0. 10 §2 |
| C1 | G01 trusts an orchestrator-written reproduction row | S | The broker recomputes signatures from attested executions. 03 §5, 04 §4 |
| C2 | Orchestrator-written risk sets the approver count | S | The API recomputes risk; a mismatch is refused. 04 §4 |
| C3 | The API alone can mint approvals | S | Passkey assertions verified by the deployer (ADR-015) |
| C4 | Audit hash depends on the session timezone; global lock contention | S | UTC epoch microseconds; per-tenant chains. 02 §3 |
| C5 | Socket proxies can't enforce parameters | S | Broker plus constructor is the policy point; dedicated sandbox host. 04 §5 |
| C6 | Signature extraction needs structured data | A | Trusted `sentinel-pytest` plugin plus cross-checks. 03 §5 |
| C7 | Benchmark leakage through git history | S | Clean repository per trial. 10 §3, AT-EVAL-03 |
| C8 | No AppArmor or `runsc` on a Windows Docker host | C | A Linux sandbox host; spike decision rules. 04 §5 |
| C9 | No OIDC subject column; GitHub OAuth isn't OIDC | C | `oidc_issuer` and `oidc_subject`; an OIDC provider (Keycloak in dev). 02 §4 |
| D1 | No tracking of in-flight executions | A | `execution_requests` and an async broker (ADR-020) |
| D2 | Broker source tree undefined | A | Codeintel archive endpoint. 05 §3 |
| D3 | Hidden-test runner undefined | A | `r_harness` role and harness runner. 01 §3 |
| D4 | No backup or restore | A | NFR-11, 07 §3 |
| D5 | No notifications | A | FR-30 outbound webhook (MVP) |
| D6 | Humans can't contribute evidence mid-run | A | FR-33 run notes |
| D7 | One execution per gate | C | `verification_check_executions` join table |
| E1 | Bring-your-own telemetry | A | FR-32: port in the MVP, adapters in V1 |
| E2 | Delivery modes | A | FR-31, ADR-021 |
| E3 | Redis in the control plane | C | Postgres LISTEN/NOTIFY (ADR-022). Redis stays in the target application as Valkey. |

## 0.4 Contradictions between D1 and D2, and inside D2

| ID | Contradiction | Dir | Resolution |
|---|---|---|---|
| R-01 | D2 §4.5 uses outcome `ESCALATED_INTERNAL`, which is missing from the §8.5 outcome list and the `run_outcome` enum | C | New outcome `ESCALATED_DEPENDENCY_UNAVAILABLE` for when a dependency is unreachable after retries. `FAILED_INTERNAL` stays reserved for Sentinel bugs. 03 §3 |
| R-02 | D2 §3.1 says codeintel owns `repo_revisions`, but §3.2 defines no codeintel role | C | Role `r_codeintel` added. 02 §5 |
| R-03 | D1 §7 puts code-vs-operational routing under Capability D (V1); D2 builds REPORT_ONLY routing in Phase 3 | C | Routing and abstention are **MVP**. Only *delivery* is V1. 09 |
| R-04 | D1 §14 lists run replay as V1; D2 B46 builds it in Phase 5, and CI needs it for recorded responses | C | Replay is **MVP**. 09 |
| R-05 | D1 §14 lists the security test suite as V1; D2 B34 builds the escape suite in Phase 4 | C | The escape suite is **MVP**; the full V1 security review stays V1. 09 |
| R-06 | D2 §15.3 says Tier 2 (gVisor) must run "before any injection benchmark", but injection scenario S35 is in Wave 1 (MVP) while gVisor (B55) is in Phase 7 | S | **gVisor moves into Phase 4**, before S35 runs. 10 §3 |
| R-07 | D1 §11 says the second approver for high-risk patches is "configurable"; D2 §10.5 fixes two approvers for HIGH | S | Minimum 2 for HIGH, configurable **upward only**. Lowering isn't supported. 04 §6 |
| R-08 | D1 Capability B lists property-based tests as a strategy; D2 S3 covers concurrency stress only | C | S3 = property-based **or** concurrency stress. 03 §5 |
| R-09 | D2 §13.10 says "row-level security not required"; multi-tenancy (OD-12) needs it | S | RLS on every tenant-scoped table, **in addition to** role separation (ADR-019) |
| R-10 | D2 §5.3 batches with `COPY`, but PostgreSQL rejects `COPY FROM` for roles subject to RLS | C | Batched `INSERT … SELECT FROM unnest(…)`, with throughput verified by AT-ING-01 |
| R-11 | D2 §8.6 and §18.10 budget in USD only; free models cost 0 USD but have hard token and request quotas | S | Budgets add tokens and requests per provider. USD kept. 03 §4 |
| R-12 | D2 §12 / §13.8 use a fixed `vector(1024)`; the local embedding model sets the dimension | C | Embeddings table keyed by model, with an index per model. 02 §4 |
| R-13 | D2 §8.12 leases both the job and the run | C | The job lease is authoritative; the run's lease fields mirror it for display |
| R-14 | D2 §11.3 makes Sentinel run canary on production, but real adopters' CD owns production | C | Two delivery modes (ADR-021). FR-15 stays fully required in managed mode for the reference environment. |
| R-15 | D2 §9.3 uses `--timeout`, which needs `pytest-timeout`, absent from the image spec | C | Added to the trusted base image along with the `sentinel-pytest` plugin |
| R-16 | D2 §2.3 moves the sandbox host to its own VM "in V1"; on a Windows workstation it must be separate from day one | S | A separate Linux sandbox host from Phase 0 (spike) and Phase 4 (production use) |
| R-17 | D2 §14.1 asks for re-authentication via OIDC; GitHub OAuth can't enforce `max_age` | C | An OIDC provider with `max_age`/`prompt=login` in the MVP; passkey step-up in V1 |
| R-18 | D2 §4 F9 and §3.1 use Redis for fan-out; nothing else in the control plane needs it | C | LISTEN/NOTIFY (ADR-022). One less control-plane dependency. |

## 0.5 New requirements

Defined in `12-traceability.md` §2: FR-21 to FR-33 (tenancy, privacy, provider independence, embeddings, independent evaluation, the four additions, notifications, delivery modes, telemetry sources, run notes) and NFR-11 to NFR-20 (Sentinel's own reliability, burst ingest, tenant isolation, licences, key rotation, quota fairness).
