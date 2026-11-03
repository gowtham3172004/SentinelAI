# 11. Definition of done and acceptance-test catalogue

Supplies D2's missing "Section 23".

**Every component is done only when all of the following hold:**
1. Its acceptance tests (AT) pass in CI.
2. Its code meets the coverage targets: domain ≥ 90%, repository ≥ 80% (D2 §19.2).
3. Its public interfaces are documented.
4. Its audit actions are listed in the action catalogue.
5. Its failure modes appear in 07 §2.

## 1. Acceptance-test catalogue

Test location convention: `tests/<layer>/…`, with each test marked `@pytest.mark.at("AT-XXX-nn")` so CI can report AT coverage.

### Foundation
| ID | Test |
|---|---|
| AT-DB-01 | Migrations apply up → down → up on an empty database; expand-and-contract linter passes |
| AT-DB-02 | Generated test per authority-matrix cell: allowed operations succeed, all others fail with permission denied |
| AT-DB-03 | Insert-only tables reject UPDATE, DELETE and TRUNCATE for every application role |
| AT-DB-04 | `one_active_run` enforced |
| AT-DB-05 | A gate PASS without a linked execution fails at commit (except G11 and G14) |
| AT-AUD-01 | The audit chain verifies; tampering with any field of any row → the verifier reports the first broken row |
| AT-AUD-02 | The chain verifies identically with session TimeZone set to UTC, Asia/Kolkata and America/Los_Angeles |
| AT-AUD-03 | 16 concurrent writers across 2 tenants → both chains valid and gap-free; appending to another tenant's chain is refused |
| AT-AUD-04 | Every action in the catalogue produces exactly one audit row in its integration test |
| AT-TEN-01 | For every tenant-scoped table: a session in tenant A sees zero rows of tenant B and can't insert, update or delete them |
| AT-TEN-02 | A query without tenant context raises (fails closed) for every tenant-scoped table |
| AT-TEN-03 | Property test: random (role, tenant, table, operation) sequences never cross tenants |
| AT-TEN-04 | Executions and containers carry tenant labels; a runner can't see another tenant's workspace |
| AT-TEN-05 | Per-tenant quotas enforced (runs, tokens, requests, executions); one tenant at quota doesn't delay another |
| AT-Q-01 | 16 workers × 10k jobs: no double claim, no lost job |
| AT-Q-02 | Lease expiry requeues; `max_attempts` → `dead` + alert event |
| AT-Q-03 | Duplicate `dedupe_key` rejected while queued or running |
| AT-Q-04 | Outbox: crash between publish and mark → redelivered and deduplicated by `event_id` |
| AT-LIC-01 | The licence report passes the allow-list (Python and npm) |
| AT-SEC-01 | Static stage blocks on ruff, mypy, import-linter, bandit, semgrep/opengrep, gitleaks, pip-audit |
| AT-SEC-02 | Injection corpus: unsafe-action rate is 0 |
| AT-SEC-03 | A service token for the wrong service or tenant is rejected at every internal endpoint |

### Telemetry, detection, incidents
| ID | Test |
|---|---|
| AT-ING-01 | 1,000 events/s for 10 minutes with RLS on; p95 ingest lag < 5 s (NFR-07, NFR-16) |
| AT-ING-02 | 2,000 events/s burst: 100% of error and exception records stored; shed counts reported |
| AT-ING-03 | Planted-secret corpus never appears in any table or blob |
| AT-ING-04 | Malformed records go to the dead-letter table with a reason; clock skew over 5 s flagged |
| AT-ING-05 | Redaction is idempotent and never leaks the generated secret patterns (property test) |
| AT-FP-01 | Fingerprint stable under changed line numbers, IDs and values (property test) |
| AT-FP-02 | Distinct bugs in the corpus get distinct fingerprints |
| AT-FP-03 | `library_only` set when no frame is in-app |
| AT-DET-01 | Precision ≥ 0.90, recall ≥ 0.95 on Wave 1 timelines |
| AT-DET-02 | Detection latency ≤ 90 s |
| AT-DET-03 | Baselines exclude windows belonging to an open incident |
| AT-INC-01 | One incident per fault; duplicates attach; severity only rises |
| AT-INC-02 | Recurrence within 30 minutes reopens with the same ID |
| AT-INC-03 | `incident_transition()` rejects every illegal or unauthorised (from, to, role) combination (enumerated) |
| AT-INC-04 | Suppression, noise rules and the umbrella incident at the rate limit |
| AT-INC-05 | Self-resolution path; the active run continues with `self_resolved` |
| AT-REL-EV-01 | Release webhook: bad HMAC, a stale timestamp and a replayed idempotency key are all rejected |
| AT-NOT-01 | Notification delivered at least once, signed, retried with backoff, deduplicated by `event_id` |

### Investigation
| ID | Test |
|---|---|
| AT-CI-01 | Golden query set returns correct results at a pinned revision |
| AT-CI-02 | Never reads a working tree; a planted git hook or filter never executes |
| AT-CI-03 | Outputs bounded with a `truncated` flag |
| AT-CI-04 | Archive tar hash is stable and matches the revision |
| AT-CI-05 | Frame mapper emits `revision_mismatch` |
| AT-CI-06 | Pre-ranker top-3 localisation reported on Wave 1 code scenarios (D1 target ≥ 85%) |
| AT-LLM-01 | Schema-failure injection: one repair, then the step fails per the transition table |
| AT-LLM-02 | Property test: overspending tokens, requests or USD is impossible |
| AT-LLM-03 | Every call recorded with prompt hash, input hash, provider model version and egress decision |
| AT-LLM-04 | Planted secrets never appear in outbound payloads (recording proxy) |
| AT-LLM-05 | A provider not allowed for a data class by the tenant policy is never called |
| AT-LLM-06 | 429 / `retry-after` / daily cap → run parks, doesn't consume wall-clock, resumes; exceeding `max_park` → `ESCALATED_DEPENDENCY_UNAVAILABLE` |
| AT-LLM-07 | Adapter contract suite passes against the Groq and second-provider adapters (recorded responses) |
| AT-LLM-08 | A model without `json_schema` falls back to tools or `json_object` + validation |
| AT-LLM-09 | Capability probe marks retired models unavailable; failover recorded |
| AT-EMB-01 | Switching the embedder schedules re-embedding; dimensions enforced; per-model index used |
| AT-WF-01 | Every transition in the table executes; every other transition is rejected |
| AT-WF-02 | Model-based test: every state reachable; every non-terminal state reaches a terminal one |
| AT-WF-03 | Kill the worker in each state → resume with no duplicate side effects |
| AT-WF-04 | Each hard budget → `ESCALATED_BUDGET` with a report; soft cap disables optional work |
| AT-WF-05 | Tool-policy denials recorded; 3 violations → `ESCALATED_POLICY` |
| AT-WF-06 | Strict replay reproduces a recorded run exactly; lenient replay records a branch |
| AT-WF-07 | A run pinned to the old `workflow_version` survives an upgrade per 03 §2 |
| AT-WF-08 | Run notes become `human_note` evidence at the next boundary and never count toward Supported |
| AT-EV-01 | Bundle immutable after freeze; manifest hash stable |
| AT-EV-02 | Size caps; untrusted wrapping; content containing the delimiter is rejected |
| AT-TRI-01 | Below the evidence floor → `ESCALATED_INSUFFICIENT_EVIDENCE` |
| AT-TRI-02 | Prior and model label both stored; an override without a citation is rejected |
| AT-HYP-01 | Invalid citations rejected (not corrected) |
| AT-HYP-02 | Fewer than 2 hypotheses, or no non-code alternative → rejected |
| AT-HYP-03 | Temporal, release, changed-file and location consistency checks fire on fixtures |
| AT-PRD-01 | Every predicate's evaluator unit- and property-tested; unknown predicate or reference → `invalid` |
| AT-PRD-02 | Discriminating-ness computed only by the system; battery expectations come from the class table |
| AT-PRD-03 | Supported impossible without a confirmed battery discriminator; a refutation caps at Hypothesis |
| AT-PRD-04 | S01: the pool-pressure rival is refuted by `precedes` |
| AT-LVL-01 | `compute_level` is pure; no code path lets model output set a level |
| AT-RPT-01 | Report citations valid; hash stable; four buckets present; limitations match gate results |
| AT-RPT-02 | Every terminal outcome yields a report (including budget, policy and dependency escalations) |

### Sandbox, reproduction, patch, verification
| ID | Test |
|---|---|
| AT-BRK-01 | Attested record verifies; a tampered record is rejected by the verifier |
| AT-BRK-02 | A repeated idempotency key returns the stored result |
| AT-BRK-03 | Broker restart mid-execution → request recovered or `killed`; at most one retry |
| AT-BRK-04 | Only template IDs accepted; parameter patterns enforced; command strings impossible |
| AT-BRK-05 | Tar validator rejects absolute paths, `..`, escaping links, device nodes, setuid bits, oversize archives |
| AT-BRK-06 | A forged `reproductions` row is ignored; G01/G03 use the broker's recomputed signature |
| AT-BRK-07 | A plugin report that disagrees with JUnit or the collection count → `report_mismatch` FAIL + `integrity_suspected` |
| AT-SBX-01…13 | One test per row of D2 §15.10 (network, environment, host files, filesystem, fork bomb, memory bomb, disk fill, output flood, tar attack, privilege, persistence, cross-run, result forgery) |
| AT-SBX-14 | `build_sandbox_config()` asserts every hardening flag; no caller-supplied options |
| AT-SBX-15 | AT-SBX-01…13 pass under `runsc` |
| AT-REP-01 | S01 reproduces with a signature match |
| AT-REP-02 | A test with a mismatched signature is rejected and the mismatch fed back |
| AT-REP-03 | 1–2 failures out of 3 → `INCONCLUSIVE` |
| AT-REP-04 | Zero false reproductions (03 §5 definition) |
| AT-REP-05 | `NOT_REPRODUCIBLE_HERE` only for the policy-listed reasons |
| AT-PAT-01 | Every forbidden path rejected by the pre-check |
| AT-PAT-02 | Every forbidden construct rejected by the AST scan; flagged constructs flagged |
| AT-PAT-03 | Retries bounded; the regression test is never modified by PATCH or DIAGNOSE |
| AT-PAT-04 | Advisory patches are never eligible for approval |
| AT-VER-01 | Seeded bad patches rejected: deleted test, weakened assertion, out-of-scope file, secret in diff, early exit |
| AT-VER-02 | A good patch reaches `VERIFIED` |
| AT-VER-03 | Property test: adding a failing gate never improves status |
| AT-VER-04 | Baseline failures listed as pre-existing, not blamed |
| AT-VER-05 | Only `r_broker` can write verification rows (role test) |

### Risk, approval, proof, UI, delivery
| ID | Test |
|---|---|
| AT-RSK-01 | Factor table unit-tested, including the HIGH override rule |
| AT-RSK-02 | The API refuses a decision when the recomputed risk differs; HIGH needs 2 distinct approvers |
| AT-APR-01 | Deployer refuses: tampered diff, tampered report, expired, replayed `jti`, wrong scope, wrong `base_sha`, unknown `kid`, wrong tenant |
| AT-APR-02 | Separation of duties; minimums configurable only upward |
| AT-APR-03 | Re-authentication window enforced |
| AT-APR-04 | Passkey: assertion over a different challenge refused by the deployer; credential inside cool-down refused; revoked refused |
| AT-APR-05 | Decision refused unless verification is `verified` or `verified_with_limitations` |
| AT-PRF-01 | `proof verify` passes on a genuine run |
| AT-PRF-02 | Tampering with any link, signature, `prev_link` or subject → fails and names the link |
| AT-PRF-03 | Verification works offline with only the bundle's embedded JWKS |
| AT-UI-01 | Playwright: view incident → review diff → approve |
| AT-UI-02 | A new incident appears in the browser within 5 s |
| AT-UI-03 | Role-by-endpoint matrix enforced (viewer, responder, approver, admin, platform admin) |
| AT-DLV-01 | PR-only: the PR carries the report and proof; CI status recorded |
| AT-DLV-02 | Merge-base→head patch ≠ approved diff → detected and refused |
| AT-DLV-03 | Staging health checks: healthy passes, broken fails |
| AT-DLV-04 | Canary steps with a separate promotion approval |
| AT-DLV-05 | Unhealthy build rolls back automatically in CI within the window |
| AT-DLV-06 | Deployer crash → `failed_safe` + rollback attempt on restart |
| AT-DLV-07 | Post-deploy watch resolves on absence and reopens on recurrence |

### Knowledge base, evaluation, privacy, operations
| ID | Test |
|---|---|
| AT-KB-01 | Precision@3 measured on labelled pairs |
| AT-KB-02 | Advisory cases never count toward Supported |
| AT-KB-03 | Staleness flags correct at the pinned revision |
| AT-KB-04 | Leakage control: a trial's KB holds only earlier dev cases |
| AT-EVAL-01 | Oracle scores 1.0, null system 0, always-abstain shows the correct and false abstention pair |
| AT-EVAL-02 | Hidden tests and ground truth unreachable from every Sentinel role, network and filesystem |
| AT-EVAL-03 | Trial repository contains no commits after the faulty release |
| AT-EVAL-04 | Judge calibration agreement on 30 human-labelled items reported; judge family ≠ strong-tier family |
| AT-EVAL-05 | Each trial records models, prompt versions, image digests and seeds |
| AT-EVAL-06 | Bootstrap confidence intervals and the risk-coverage curve produced from raw data |
| AT-PRV-01 | Retention jobs drop data beyond policy; evidence copies survive |
| AT-PRV-02 | Tenant erasure removes all tenant rows and blobs; remaining audit rows contain no P2/P3; chains still verify |
| AT-PRV-03 | P3 never leaves the trust zone; P2 egress only to allowed providers |
| AT-PRV-04 | Audit payloads validated against the per-action allow-list |
| AT-OPS-01 | Restore drill: latest backup → fresh DB → audit verify + smoke test pass; backup age alert |
| AT-OPS-02 | SIGTERM drains within 30 s; no lost or duplicated work |
| AT-OPS-03 | Startup reconciliation removes orphaned containers and moves stuck deliveries to `failed_safe` |
| AT-OPS-04 | Provider outage: detection, incidents and UI unaffected; runs park, then escalate |
| AT-OPS-05 | 60 s database outage: all services recover without manual action; no duplicate side effects |
| AT-OPS-06 | A missing heartbeat triggers the dead-man alert |
| AT-OPS-07 | Key rotation: a new `kid` signs; old tokens and links verify until expiry |
| AT-OPS-08 | One command brings up the stack; every service answers `/healthz` and `/readyz` |
| AT-OPS-09 | Sentinel's own telemetry never opens incidents |

## 2. Component definition of done

| Component | AT set |
|---|---|
| Schema, roles, RLS, audit | AT-DB-*, AT-AUD-*, AT-TEN-01…03, AT-PRV-04 |
| Queue and outbox | AT-Q-*, AT-OPS-02 |
| Ingest and fingerprinting | AT-ING-*, AT-FP-* |
| Detector and incidents | AT-DET-*, AT-INC-*, AT-REL-EV-01, AT-NOT-01, AT-OPS-09 |
| Codeintel | AT-CI-* |
| LLM gateway and embeddings | AT-LLM-*, AT-EMB-01, AT-PRV-03 |
| Workflow engine and handlers | AT-WF-*, AT-EV-*, AT-TRI-*, AT-HYP-*, AT-LVL-01, AT-RPT-* |
| Prediction engine | AT-PRD-* |
| Broker and sandbox | AT-BRK-*, AT-SBX-*, AT-TEN-04 |
| Reproduction and patching | AT-REP-*, AT-PAT-* |
| Verification and risk | AT-VER-*, AT-RSK-* |
| Approvals and passkeys | AT-APR-* |
| Proof bundles | AT-PRF-* |
| Dashboard | AT-UI-* |
| Deployer | AT-DLV-*, AT-OPS-03 |
| Knowledge base | AT-KB-* |
| Harness | AT-EVAL-* |
| Operations | AT-OPS-*, AT-PRV-01/02, AT-TEN-05, AT-LIC-01, AT-SEC-* |

## 3. MVP definition of done (D2's missing §23.1)

The MVP is done when **all** of the following hold:

1. Every AT in the MVP components passes in CI on `main`. "MVP components" means everything in 09 §1: all rows of §2 above except deployer, passkeys (AT-APR-04), knowledge base, and the V1 parts of operations (point-in-time recovery, AT-OPS-07, AT-PRV-02).
2. The sandbox escape suite passes on Tier 1 **and** Tier 2 on the dedicated sandbox host.
3. The benchmark campaign has run on all 15 Wave 1 scenarios for B0, B1, B2 and B3, with recorded-mode trials as specified (or the documented reduced count if RK-01 applies; the reduction must be reported, not hidden). The report includes:
   - verification false-positive and false-negative rates;
   - paired correct/false abstention;
   - the risk-coverage curve;
   - zero false reproductions;
   - an unsafe-action rate of 0 on S35;
   - cost (USD, tokens, requests) and time per trial.

   Results are labelled **dev-split** except S02, S06 and S10.
4. **End-to-end demo** from a clean checkout:
   - inject S01 → incident → investigation with the rejected pool-pressure rival;
   - reproduction → patch → verification matrix → risk;
   - signed approval → `sentinelai proof verify` passes;
   - and, separately, S33 ends in REPORT_ONLY with no patch.
5. A restore drill has passed within the last 24 h. The audit chains verify.
6. Threat model reviewed against what was built. The traceability matrix (12) shows every MVP requirement with a passing AT.
7. Release tag `v0.5-mvp` with the AT pass list, benchmark artefacts and recorded responses archived.

## 4. V1 definition of done

The MVP definition of done, plus:
- Every AT in the remaining components passes.
- Both delivery modes are demonstrated (AT-DLV-*), including an automatic rollback.
- Passkey consent is verified by the deployer.
- The knowledge-base ablation has run.
- All 40 scenarios are validated, and the full campaigns with ablations and human review are complete.
- The open benchmark is published.
- The point-in-time-recovery RPO has been measured.
- All runbooks are written.
- Security review findings are fixed or documented.
- Release tag `v1.0`.
