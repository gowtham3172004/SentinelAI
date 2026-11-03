# 03. Workflow amendments and the prediction engine

Amends D2 §8 and §9. The state set, the transition table (D2 §8.4), the tool permissions (§8.7) and the context rules (§8.9) are **unchanged** except as stated here.

## 1. What doesn't change

- The explicit finite state machine stored as data.
- The engine performs transitions.
- Every transition not in the table is rejected.
- All D2 counters and limits.
- Untrusted-content wrapping.
- At least two hypotheses, including a non-code alternative.
- The citation validator and the consistency checks.
- The failure-signature rule.
- The scope policy.
- The bounded retry loop.
- The regression test is never edited.

## 2. Additions to the transition logic

| Addition | Rule |
|---|---|
| **Parking** (not a state) | When the gateway can't schedule a call within limits, the handler returns `Park(until, reason)`. The engine commits a step with trigger `parked`, sets `parked_until`, and defers the job (`run_after`). Parked time is excluded from the wall-clock budget up to `max_park` (initially 6 h). After that: `REPORT` with `ESCALATED_DEPENDENCY_UNAVAILABLE`. |
| **Dependency unavailable** | Broker or codeintel unreachable after the idempotent retry limit (D2 §4.5): `REPORT` with `ESCALATED_DEPENDENCY_UNAVAILABLE` (R-01) |
| **Self-resolved incident** | The run is **not** stopped. `self_resolved=true` is recorded, and the report states it. A human may cancel (`DONE_CANCELLED`, unchanged). |
| **Run notes** | At the entry of `CORRELATE`, `HYPOTHESIZE`, `VALIDATE` and `DIAGNOSE_FAILURE`, un-ingested `run_notes` become `human_note` evidence items (trust `human`): post-freeze, citable, **never counted** toward Supported |
| **Cancel** | `cancel_requested_at` is checked before every model call, tool call and execution (D2 §8.4 "human cancellation") |
| **Workflow versioning** | Each run pins `workflow_version`. After an upgrade, in-flight runs either finish on their pinned definition (if it's still loadable) or move to `REPORT` with `FAILED_INTERNAL` reason `workflow_retired`. Never silently re-interpreted (NFR-14). |

## 3. Terminal outcomes

D2 §8.5 plus:

| Outcome | Meaning | Human action |
|---|---|---|
| `ESCALATED_DEPENDENCY_UNAVAILABLE` | A required dependency (model provider quota, broker, codeintel) stayed unavailable beyond its limits | Re-run when the dependency is back. The partial report lists what was established. |

## 4. Budgets (R-11)

D2 §8.6 limits all remain. Added hard limits per run (initial values):

| Limit | Value |
|---|---|
| Input tokens | 400k |
| Output tokens | 60k |
| Model requests | 60 (unchanged) |
| Requests per provider per minute | from the registry |
| Max park | 6 h |

USD is still computed from the price table, which is 0 for free tiers, and the D2 caps still apply. **Per-tenant daily quotas** (NFR-20) are checked before each call, alongside the run budget.

## 5. Reproduction and signature changes

1. **Trusted plugin.** The sandbox base image includes `sentinel-pytest` (our code, built in the trusted image step). It writes `/out/sentinel-report.json`: per test, outcome, exception type, the full traceback as `{path, function, line}` frames, and the collected-test count.
2. **The signature is computed twice.** The orchestrator computes it to drive the loop. The **broker recomputes it independently** from attested executions, using the same pure function `domain.signature.match()`, when it evaluates G01 and G03. Gate results use **only** the broker's computation (C1).
3. **Cross-checks against plugin forgery** (C6). The plugin report must agree with the JUnit XML on test IDs and outcomes, and with the collection count (G05). A disagreement makes the gate `FAIL` with reason `report_mismatch` and raises an `integrity_suspected` flag.
4. **S3 strategy** = property-based (Hypothesis) **or** barrier-synchronised concurrency stress (R-08). The threshold is unchanged.
5. **False reproduction** (B7) means: a reproduction accepted (outcome REPRODUCED) whose observed signature doesn't match the ground-truth failure mechanism, **or** any accepted reproduction on a scenario flagged `reproducible: false` *because the environment can't host it* (`NOT_REPRODUCIBLE_HERE` class). A genuine, signature-matching reproduction of a rare race (S38) is scored as correct, not false.

## 6. The prediction engine (ADR-013)

### 6.1 Purpose

D2 §8.10 asks for "predicted observations that would confirm or refute" each hypothesis, as free text. The prediction engine makes those predictions **executable and system-evaluated**, so that *Supported* means "a test that could have failed didn't", not just "the model cited plausible evidence".

### 6.2 Query language

Predictions are **JSON abstract syntax trees** validated by a Pydantic discriminated union. There's no text parser. Each query is a predicate over the frozen bundle's time range, using system data only.

| Predicate | Arguments | Evaluates to true when |
|---|---|---|
| `precedes` | `a: Ref, b: Ref, min_gap_s=0` | onset(a) + gap ≤ onset(b) |
| `release_correlated` | `fingerprint, within_min=30` | First seen within N minutes after a deploy of the service |
| `config_change_precedes` | `within_min=30` | A config change for the service within N minutes before incident onset |
| `in_suspect_range` | `path, symbol?` | The file or function changed between the previous and the deployed revision |
| `exists_at_revision` | `path, symbol?` | The location exists at the pinned revision |
| `count_compare` | `fingerprint, window, filter?{release_version, endpoint}, op, value` | The occurrence count satisfies `op value` |
| `metric_shift` | `metric, labels?, before, after, stat∈{mean,p95,max}, op∈{increases_by,decreases_by,within}, factor` | The stat moved as stated between the two windows |
| `trace_share` | `fingerprint, span_service?, span_name?, error:bool, op, share` | The share of incident traces containing such a span satisfies `op share` |
| `co_occurs` | `a: fingerprint, b: fingerprint, within_s, min_share` | At least `min_share` of a's occurrences have b within `within_s` |
| `downstream_error_share` | `dependency, op, share` | Share of failing requests with an erroring downstream client span |
| `multi_service_errors` | `error_class, min_services=2` | The same connection-error class across at least N services |
| `dependency_diff_nonempty` | none | The lockfile differs between the previous and the deployed revision |

- `Ref` = `{kind: fingerprint|signal|metric_onset|release|config_change|evidence, id}`. Onset times come from the deterministic timeline builder (D2 §7.7).
- **Results:** `true`, `false`, `insufficient_data` (volume floor not met, or the data is outside the bundle), or `invalid` (it refers to something that doesn't exist).
- Every evaluation produces a system-trust evidence item of kind `prediction_result`, which can be cited.

### 6.3 Prediction matrix

`HYPOTHESIZE` outputs, alongside the hypotheses, a **prediction matrix**: a set of queries, and for each hypothesis an expectation per query (`true` / `false` / `indifferent`). The engine then:

1. **Adds the system battery.** It adds a fixed set of queries whose expectations come from a deterministic **class expectation table** (§6.4), not from the model. That's what stops the model gaming discriminating-ness with straw-man rivals.
2. **Evaluates every query deterministically** (the prediction evaluator, orchestrator process, pure core in `domain`).
3. **Computes discriminating-ness.** A query is *discriminating* for a pair of hypotheses if they hold opposite non-indifferent expectations on it.
4. **Applies the results.** For a hypothesis H:
   - Any query where H expects `true`/`false` and the result is the opposite → a **refutation**. A `contradicts` link to the `prediction_result` item is recorded, and H can't be above *Hypothesis*.
   - A query result matching H's expectation, where the query discriminates H from at least one rival → a **confirmed discriminating prediction**.

### 6.4 Class expectation table (system battery, initial version, `battery_version=1`)

Key: T = true, F = false, · = indifferent. "Code" covers both `code_defect` and `regression_from_release`.

| Query | code | configuration | dependency_change | external_dependency | infrastructure | resource_exhaustion | performance |
|---|---|---|---|---|---|---|---|
| `release_correlated(primary_fp)` | T | · | · | F | F | · | · |
| `in_suspect_range(top in_app frame)` | T | F | F | F | F | · | · |
| `precedes(error_onset, resource_onset)` | T | · | · | · | · | F | · |
| `config_change_precedes(30)` | · | T | · | F | F | · | · |
| `downstream_error_share ≥ 0.5` | F | · | · | T | · | · | · |
| `multi_service_errors(connection)` | F | · | · | · | T | · | · |
| `dependency_diff_nonempty` | · | · | T | · | · | · | · |
| `metric_shift(p95 latency, increases_by 2)` | · | · | · | · | · | · | T |

Rules about the table:
- It's versioned and stored with every run.
- Changing it needs a new ADR plus a re-run of the dev-split evaluation.
- Battery queries whose arguments can't be resolved (for example, no in-app frame) evaluate to `insufficient_data`. They are recorded, not dropped.

### 6.5 Evidence levels (amends D2 §8.10 / ADR-012)

| Level | Computed as (all conditions) |
|---|---|
| Hypothesis | Unchanged from D2 |
| **Supported** | D2's conditions (≥ 2 independent evidence kinds, no failing consistency check) **and** ≥ 1 confirmed discriminating prediction **from the system battery** **and** no refutation |
| Reproduced | Unchanged, but the signature match must also be confirmed by the broker's recomputation |
| Verified | Unchanged (broker-computed status) |

`compute_level` stays a pure function. The new condition is strictly stronger (direction S).

**Expected effect:** more abstention when evidence is weak. That's measured by the paired abstention metrics; the false-abstention target in D1 §13 still applies, and the battery is tuned on the dev split only.

### 6.6 VALIDATE (amends D2 §8.2)

- The model's role in VALIDATE becomes **query planning only**: it may propose additional queries for the matrix, within the per-state tool limits.
- Leader selection is deterministic: the highest evidence level, then the most confirmed discriminating predictions, then the pre-ranker score.
- Every transition condition in D2 §8.4 for VALIDATE stays as written; it's evaluated over the computed levels.

## 7. Report and proof

- At `REPORT`, the orchestrator builds the **investigation link** statement (06 §B.3) over: the evidence manifest hash, the pinned revision, the hypotheses with their levels, the prediction results, the reproduction execution IDs and digests, and the patch digest.
- It signs the statement with the orchestrator's key (`kid`) and stores it in `proof_links`.
- The report includes a "Proof" section with the bundle ID and the verification command.
