# Sentinel AI: Project Document

*An autonomous production reliability engineer that detects, diagnoses, tests and prepares human-approved fixes*

Version 1.0 · 30 October 2026 · Document 1 of 2 · Companion: Sentinel AI Technical Design Document and Roadmap

## How to read this document

Document 1 (this one) explains what Sentinel AI is, why it is worth building, what it will do, how it works conceptually, how success is measured and how the project is scoped and packaged as a career portfolio piece. Document 2 turns it into a build specification: architecture, service boundaries, data flow, agent state machine, database schema, sandbox security model, evaluation strategy and the exact order of implementation. This document says what and why; Document 2 says how and in what order.

Planning assumptions used in both documents: one developer working about 20–25 focused hours a week; Python as the main language; a Python reference application built as part of the project is the system under observation; models are used through a provider API; Sentinel never receives access to any real customer production system.

## 1. Executive summary

Sentinel AI is an incident-response system built around one idea: every claim it makes must be traceable to evidence, and every step from a production failure to a proposed fix must be something the system actually did, not something a language model asserted.

When a production application starts failing, Sentinel observes logs, metrics, traces and deployment records; recognises that an incident is under way; links the failure to the relevant source code and recent changes; forms explicit, competing hypotheses; tries to reproduce the failure in an isolated sandbox; drafts a minimal candidate fix only if the failure is reproduced; verifies that fix by really running tests and checks; and hands an engineer an evidence-backed recovery proposal. Nothing is deployed without a human approval that is cryptographically bound to the exact patch and verification report. When the evidence is thin, or the cause is operational rather than a code defect, Sentinel says so, recommends the right kind of response and stops.

**The project has five deliverables:**

1. A reference e-commerce application (API, background worker, payment stub, PostgreSQL, Redis), fully instrumented, with a fault-injection framework that produces realistic and reproducible incidents.
2. The Sentinel platform: ingestion, detection, incident management, evidence correlation, code intelligence, an orchestrated investigation workflow, a hardened sandbox, a verification engine, an approval and staged-deployment pipeline, and an incident knowledge base.
3. A benchmark of about 40 incident scenarios with ground truth, hidden tests, baselines and ablations.
4. A dashboard for following an investigation and for reviewing and approving a proposal.
5. Engineering documentation: architecture, security model, failure handling and evaluation results, including the failures.

**Effort:** an MVP (everything up to human approval, plus a first benchmark run) takes about 14 weeks. Full scope takes about 20 weeks; plan for 22 to absorb surprises.

**What success means.** Success is not a 100% fix rate. Success is a system whose behaviour is measured honestly, that knows when to stop, and a repository that proves both.

### The chain of evidence

The differentiator is not any single stage. Observability tools detect, AIOps tools summarise, coding agents patch. Sentinel connects all of them into one chain in which each link is checked before the next one begins.

| Link | Question answered | Evidence required before moving on |
| --- | --- | --- |
| 1. Detection | Is something wrong beyond normal noise? | Windowed error rates against a baseline; grouped exceptions |
| 2. Localisation | Where, in code and in time? | Stack frames, deployment records, git history |
| 3. Hypothesis | What is the most plausible cause, and what else could it be? | Cited evidence for and against; at least two candidate explanations |
| 4. Reproduction | Can the failure be made to happen on demand? | A test that fails on the base revision with a failure signature matching production |
| 5. Fix | What is the smallest change that alters the outcome? | A diff inside the scope policy |
| 6. Verification | Does it fix the problem without breaking anything else? | Executed test and check results, attested by the sandbox broker |
| 7. Approval | Does a human accept the remaining risk? | A signed decision bound to the diff hash and report hash |
| 8. Rollout | Is the fix healthy in a running system? | Staging health checks, canary metrics, a ready rollback |
| 9. Learning | What is worth remembering? | Confirmed cause, outcome and reviewer decision stored as a retrievable case |

## 2. The problem

At 02:15 the order API starts returning HTTP 500. The on-call engineer is paged. What happens next is mostly manual detective work: read the alert, search logs, spot a stack trace, check whether something was deployed, find the commit, open the repository, work out how to trigger the bug, write a fix and a test, run the suite, ship, and watch the graphs. Much of that work is mechanical, but all of it is time-critical and error-prone at 2 a.m.

Existing categories of tooling each cover a slice:

| Category | Does well | Missing for this problem |
| --- | --- | --- |
| Monitoring and alerting | Detects threshold breaches and pages people | No diagnosis and no awareness of code |
| AI observability and AIOps | Summarises logs, correlates events, suggests probable causes | Rarely ties a cause to a specific code location; rarely tests its own claim |
| Coding agents | Read repositories and propose tested patches when given a task | Need a well-formed task; have no production evidence; no incident judgement; tend to produce a patch whether or not one is warranted |
| Runbook automation | Executes predefined recovery steps | Only handles anticipated failures |
| CI/CD | Validates and ships a change | Does not decide which change to make |

Three failure modes make a naive “LLM reads logs and fixes code” system dangerous, and Sentinel is designed around preventing each one:

1. **Confident wrong diagnosis.** The model picks a plausible story (for example blaming a database warning that is really a consequence of the failures) and builds on it.
2. **A patch that satisfies the test but not the bug.** The model writes a reproduction that does not match the production failure, or edits tests until they pass.
3. **Unsafe automation.** A malicious log line or repository file steers the agent, generated code reaches credentials or the network, or a change is deployed without genuine human consent.

## 3. Vision, goals and non-goals

**Vision.** An AI production engineer you can trust precisely because it shows its evidence, admits what it did not check, refuses to act when it should, and cannot exceed the permissions it was given.

### Goals

| ID | Goal |
| --- | --- |
| G1 | Detect incidents from telemetry with measurable precision and recall, using deterministic and explainable rules first. |
| G2 | Produce a defensible, evidence-cited root-cause investigation for code-related incidents. |
| G3 | Reproduce failures experimentally before proposing a change, and prove the reproduction matches the production failure signature. |
| G4 | Generate minimal candidate patches and verify them by really executing tests and checks in a sandbox. |
| G5 | Distinguish code defects from operational, configuration, dependency and infrastructure incidents, respond appropriately, and abstain when evidence is insufficient. |
| G6 | Keep humans in control: signed approvals, staged deployment, health checks, rollback and a tamper-evident audit trail. |
| G7 | Learn from past incidents as evidence, never as authority. |
| G8 | Be measurable: benchmark, baselines, ablations and published failures. |

### Non-goals

| ID | Non-goal | Reason |
| --- | --- | --- |
| N1 | Replacing on-call engineers or acting as a fully autonomous production changer | Unsafe and not the point; the goal is faster, better-informed humans |
| N2 | Training or fine-tuning a foundation model | Workflow quality, not model training, is the research question |
| N3 | Any unrestricted access to production machines, in any version | Core safety boundary |
| N4 | Building a general observability platform | Storage is sized for the reference application only |
| N5 | Multi-language support in the MVP | Python targets only; the design uses language adapters so others can follow |
| N6 | Automatic schema migrations or data repair | Always a human-led recommendation |
| N7 | Claiming a patch is “100% production-ready” | Not provable; Sentinel reports evidence and limits instead |
| N8 | Kubernetes in the MVP | Containers and Docker Compose are sufficient to prove the concept |

## 4. Guiding principles

These are the non-negotiables. When a design choice is unclear, the principle that applies first wins.

1. **Evidence over assertion.** A statement without a resolvable evidence reference is rejected by the system before it reaches a report.
2. **An evidence ladder, not a confidence number.** Claims are labelled Hypothesis, Supported, Reproduced or Verified, each defined by what was actually done. A model’s self-reported confidence is never used as proof.
3. **The model proposes; the system disposes.** The LLM suggests hypotheses, tests and patches. Deterministic code decides what is permitted, what ran, what passed and when to stop.
4. **Humans hold the final key.** Approval is a real control boundary: the component that deploys verifies a signed token the investigating component cannot forge.
5. **Know when to stop.** Abstention, escalation and “no code change recommended” are first-class, measured outcomes.
6. **Treat every input as hostile.** Logs, repository files, tool output and model output are untrusted data, never instructions.
7. **Everything is auditable and replayable.** Each model call, tool call, state transition and decision is recorded, so any run can be inspected and replayed.
8. **Measure; do not demo.** Capabilities count only when the benchmark shows them, including where they fail.
9. **Small and reliable beats broad and fragile.** A few incident categories handled well are better than many handled badly.

## 5. Who uses Sentinel

| Persona | Need | How Sentinel helps |
| --- | --- | --- |
| On-call engineer | Understand and resolve an incident quickly at night | A ready investigation: evidence trail, cause hypothesis, reproduction, tested diff |
| Service owner and code reviewer | Decide whether a proposed fix is safe | The real diff, verification matrix, risks, limits and rollback plan in one view |
| SRE or engineering lead | Trust and govern automation | Permissions, approvals, audit trail, metrics on accuracy, cost and acceptance |
| Security and platform engineer | Ensure automation cannot be abused | Documented threat model, sandbox policy, secret isolation, signed approvals |
| You, the builder | Demonstrate end-to-end AI engineering | A measured, documented, secure system with a public benchmark |

## 6. The end-to-end story

This is the running example. All numbers are illustrative. The code is Python, matching the reference application, and the example is made internally consistent (see Section 21 for what was corrected from the original concept notes).

A developer ships release v1.8.4 of the order service. It contains this change:

```python
def normalize_customer_note(note):
    return note.strip()  # raises when the optional note is omitted
```

The API contract allows `customer_note` to be omitted, so orders without a note now crash.

### Stage 1: Detect

Sentinel’s detector aggregates telemetry into short windows. It sees the 5xx ratio on `POST /api/orders` rise, failed worker jobs pile up, and one exception fingerprint dominate. It checks the rise against baseline and noise, and opens an incident.

```
Incident INC-1042 · service: order-api · severity: SEV2 · detected 02:15:05
Signals (illustrative):
  - 5xx ratio on POST /api/orders: 0.2% baseline -> 18% for 3 consecutive 30 s windows
  - 43 failed order jobs in 2 minutes; 37 share one exception fingerprint
  - fingerprint first seen 02:14:52, four minutes after release v1.8.4 went live
```

### Stage 2: Freeze the evidence

Sentinel snapshots the relevant logs, traces, metrics and deployment records into an immutable evidence bundle and pins the exact repository revision that was deployed. Everything downstream cites items in this bundle, so the investigation cannot drift as live data changes.

### Stage 3: Triage

Sentinel classifies the incident as a code-defect candidate, or as operational, configuration, dependency, external or infrastructure. It also notes competing signals. In this example the logs contain a warning that database connections are nearing the limit. Sentinel records it as a rival explanation and tests it: the warning began after the errors started and tracks the retry volume, so it looks like a consequence, not a cause.

### Stage 4: Correlate and localise

The stack trace points at two frames:

```
AttributeError: 'NoneType' object has no attribute 'strip'
  shop/orders/service.py, line 88, in create_order
  shop/orders/normalize.py, line 14, in normalize_customer_note
```

Sentinel locates those frames in the pinned revision, reads the surrounding code, finds that `normalize.py` was changed in the v1.8.4 commit, looks up the API contract for the field, and lists the tests that touch this path (none cover a missing note).

### Stage 5: Hypothesise

Sentinel writes structured hypotheses, each with cited evidence for and against:

- **H1:** `normalize_customer_note` fails when the optional note is `None` (supported by the trace, the contract, the timing and the changed file).
- **H2:** Connection-pool pressure caused the failures (contradicted by timing; kept as a consequence).
- **H3:** The failure is unrelated to the release (contradicted by first-seen time and the changed file).

The evidence level is Supported, not yet Reproduced.

### Stage 6: Reproduce

In an isolated sandbox built from the deployed revision, Sentinel writes a regression test that creates an order with no note, runs it against the unmodified code and requires two things: the test fails, and its failure signature (exception type and originating function) matches the production fingerprint. Only then does H1 become Reproduced. If the failure cannot be triggered, Sentinel gathers more evidence or escalates; it does not fabricate a reproduction.

### Stage 7: Patch

Sentinel proposes the smallest change that satisfies the API contract:

```python
def normalize_customer_note(note):
    if note is None:
        return ""
    return note.strip()
```

It records the original code, the diff, why the change is needed, expected behaviour and possible side effects.

### Stage 8: Verify

A deterministic verification engine, not the model, runs the checks and records what actually happened:

| Check | Result |
| --- | --- |
| Original failure reproduced on base revision | PASS |
| Regression test passes with patch | PASS |
| Existing unit tests (compared with baseline run) | PASS |
| Integration tests | PASS |
| Type checking and linting | PASS |
| Security scan of the diff | PASS |
| Patch scope policy (no test or config tampering) | PASS |
| Regression test fails again if the patch is reverted | PASS |
| Rollback available (clean revert, no migration) | PASS |

Verification status: **Verified in sandbox**. It never says “production-ready”. Any check that could not run is listed as not performed.

### Stage 9: Report and approve

Sentinel renders an incident report from structured records: root cause with its evidence level, alternatives considered, reproduction, diff, verification matrix, risk assessment, limits, rollback plan and recommended action. An engineer reviews the actual diff and either approves, rejects or requests changes. Approval produces a signed token bound to the diff hash and report hash.

### Stage 10: Deploy, monitor, learn

Only after approval does the deployment workflow become eligible: branch and pull request, CI, staging deployment, health checks, an explicit promotion step, gradual rollout with automatic rollback on unhealthy signals, and post-deployment monitoring. The outcome and the reviewer’s decision are stored in the incident knowledge base for future retrieval.

## 7. The five capabilities

Sentinel is organised around five capabilities. Each has a clear purpose, defined outputs and a scope tier: **MVP** (ready by about week 14), **V1** (about week 20) or **Stretch** (only if time remains).

### Information Sentinel correlates

| Source | What Sentinel learns | Used for |
| --- | --- | --- |
| Application logs | What failed and where | Detection, localisation |
| Stack traces | Which execution paths raised exceptions | Localisation, failure signature |
| Metrics | Whether the failure is isolated or widespread, and how severe | Detection, triage |
| Distributed traces | How a request moved between services | Localisation, triage |
| Git history | What changed before the incident | Correlation |
| Source code | Which implementation paths are relevant | Localisation, patching |
| Test results | Whether the suspected defect reproduces and whether a fix works | Reproduction, verification |
| Deployment records | Which versions were introduced, and when | Correlation |
| Database metrics | Whether the problem may originate in database behaviour | Triage |

A rise in HTTP 500s alone proves nothing about a code change. The diagnosis becomes strong only when several independent sources agree: errors began right after a deployment, most failures share one stack trace, the trace points at a changed function, and a regression test reproduces the problem.

### Capability A: Incident-to-code correlation (MVP)

**Purpose.** Answer the question that matters: which part of the application caused this incident, what evidence supports that, and what can we test to check it?

**How it works.**

- Exceptions are normalised into a **fingerprint** (type, in-application frames, masked message) so thousands of log lines collapse into a few distinct failures.
- Stack frames are mapped onto the pinned repository revision. Frames in application code are separated from library frames.
- For each candidate file, Sentinel reads git history inside a time window before the fingerprint first appeared, and checks whether the deployed commit touched it.
- Endpoint-to-handler maps, the API contract and the data model are looked up through code intelligence.
- Existing tests covering the location are listed, which also reveals what is not tested.
- Search is exact first: ripgrep, symbol index, syntax-tree queries and git. Embeddings are a fallback, not the foundation.

**Outputs.** A ranked list of code locations, each linked to evidence items, and a timeline joining the first-seen time to deployments and configuration changes.

**Done when.** On the code-defect scenarios, the true location appears in Sentinel’s top three candidates at least 85% of the time (initial target), and every location cites evidence.

### Capability B: Autonomous reproduction and experimentation (MVP)

**Purpose.** Replace “the model thinks this is the bug” with “we made the bug happen on demand”.

**How it works.**

```
Observed failure → hypothesis → relevant code → construct reproduction
      → run against ORIGINAL revision
           ├─ failure reproduced AND signature matches → generate patch
           └─ not reproduced → gather more evidence, or stop and escalate
```

Reproduction strategies, tried in order of cost: a unit test calling the failing function with arguments implied by the trace; a replay of a sanitised captured request against the sandboxed application; a property-based or concurrency-stress test; a small integration stack in an isolated network.

**The failure signature rule.** A reproduction counts only if the test fails on the unmodified base revision and the failure matches the production fingerprint (same exception type and same originating function). This prevents a test that fails for a different reason from being passed off as a reproduction.

**Honest outcomes.** `REPRODUCED`, `NOT_REPRODUCED`, `NOT_REPRODUCIBLE_HERE` (for example it needs real production infrastructure) and `INCONCLUSIVE`. Sentinel never invents a successful reproduction.

**Done when.** On reproducible scenarios, the reproduction rate meets target and no reproduction is accepted without a signature match.

### Capability C: Evidence-backed patch verification (MVP)

**Purpose.** A passing test suite does not prove a patch is production-ready. The tests may be incomplete, the reproduction may not represent the real failure, or the patch may add a security problem. Verification therefore produces a structured, honest report.

**The gates** (full definitions in Document 2): original failure reproduced; regression test passes after the patch; the regression test fails again when the patch is reverted; existing tests pass against a baseline; type checking; linting; static security analysis; secret and dependency scan of the diff; patch scope policy (no test, configuration or lockfile tampering); flakiness check; relevant integration tests; rollback availability.

**Result vocabulary.** `PASS`, `FAIL`, `UNKNOWN`, `SKIPPED` (by policy) and `NOT_APPLICABLE`. `UNKNOWN` is never counted as `PASS`. If integration tests could not run because a dependency was unavailable, the report says so prominently.

**Hard rule.** A check can only be `PASS` if a recorded, sandbox-attested execution backs it. The model’s opinion that a fix “looks right” has no standing.

### Capability D: Safe, human-gated recovery (V1)

**Purpose.** Turn a verified proposal into a controlled recovery, without removing the human from the loop.

```
Verified patch → risk assessment → human approval (signed)
   → branch + pull request + CI → staging deployment → health checks
        ├─ healthy   → explicit promotion → gradual rollout → monitoring
        └─ unhealthy → automatic rollback → escalation
```

Sentinel also separates **code defects from operational incidents**. A database outage, exhausted connection pool, expired certificate or overloaded dependency calls for an operational response, not a source change. In those cases Sentinel produces a report with recommended checks and, where relevant, a recommended immediate mitigation (for example rolling back a release). Recommendations are never executed automatically in the MVP or V1.

**Done when.** An approved patch passes through a reproducible staging pipeline, a deliberately unhealthy deployment triggers rollback, and the audit log shows every step.

### Capability E: Learning from previous incidents (V1)

**Purpose.** Reuse institutional memory as evidence, never as authority.

**How it works.** Every closed incident is stored as a case: summary, class, mechanism, code paths, confirmed root cause, resolution, outcome and reviewer decision. On a new incident Sentinel retrieves candidates using exact fingerprint matches, structured filters and, last, embedding similarity over case summaries. A retrieved case enters the evidence bundle as an **advisory** item and must be validated against the current code: does the function still exist, does the mechanism apply, did the fix survive?

**Guardrails.** Cases from other application versions are flagged as possibly stale. The benchmark controls for leakage by seeding the knowledge base only with earlier, dev-split scenarios, and an ablation measures whether the knowledge base actually helps.

## 8. The evidence and trust model

This model is the intellectual core of the project. It replaces vague confidence statements such as “high, pending reproduction” with levels defined by what was actually done.

### The evidence ladder

| Level | Meaning | Minimum evidence |
| --- | --- | --- |
| **Hypothesis** | A plausible explanation | At least one cited evidence item, and at least one competing explanation recorded |
| **Supported** | Independent sources agree | At least two independent kinds of evidence (for example stack trace, git change, deployment timing) and no unrefuted contradiction |
| **Reproduced** | The failure happens on demand on the base revision | A sandbox execution of a failing test whose signature matches the production fingerprint |
| **Verified** | The candidate fix demonstrably removes the failure | Fail-to-pass and pass-to-pass results, plus all required gates `PASS`, each attested by the sandbox broker |

**Verified means verified in the sandbox against the available tests. It does not mean safe in production.**

### Four buckets in every report

1. **Verified facts:** things Sentinel observed or executed, each with an evidence or execution reference.
2. **Probable conclusions:** inferences, with their ladder level.
3. **Unresolved risks:** known unknowns, such as untested concurrency behaviour.
4. **Checks not performed:** gates that were skipped or could not run, with the reason.

### Citation enforcement

Every claim in a report that refers to evidence carries an evidence ID. A deterministic validator rejects a report whose citations do not resolve to items in the frozen bundle, or whose claims cite evidence that contradicts them. The model cannot cite evidence it was never shown.

### Verification statuses

| Status | Meaning |
| --- | --- |
| `VERIFIED` | All required gates passed |
| `VERIFIED_WITH_LIMITATIONS` | No gate failed, but some gates were `UNKNOWN` or `SKIPPED`; each is listed |
| `FAILED` | At least one required gate failed |
| `UNVERIFIED_ADVISORY` | A suggestion produced without reproduction; never eligible for deployment |

## 9. Incident classes and response routing

An intelligent production engineer must know when not to write code. Sentinel classifies each incident and routes it accordingly.

| Class | Typical signals | Sentinel’s response | Code patch? |
| --- | --- | --- | --- |
| Code defect | Consistent exception in application frames; started with a release; reproducible | Full pipeline | Yes, if reproduced |
| Regression after release | Fingerprint first seen right after a deployment; contract mismatch | Full pipeline; recommends rollback as an immediate mitigation option | Yes, or rollback |
| Configuration | Errors align with a configuration change; no defective code frames | Configuration diff and recommended revert | No |
| Dependency change | Failure follows a lockfile change; library frames dominate | Identify the upgrade; recommend adaptation, pin or rollback | Sometimes; pins are recommendations |
| Resource exhaustion | Pool, memory or file-descriptor metrics saturate | Distinguish a leak in code from a capacity limit | Sometimes |
| External dependency | Timeouts or 5xx from a downstream service | Report; suggest resilience review | No |
| Infrastructure | Database unreachable, DNS or certificate failure | Operational report and checks | No |
| Performance | Latency rise; slow query | Profile; propose a code or query change; index or migration advice only | Sometimes |
| Data issue | Malformed or unexpected data | Report; repair is human-led | No |
| Unknown | Conflicting or insufficient evidence | Abstain; list next investigations | No |

### Abstention rules

Sentinel stops and escalates, with a useful report, when:

1. No application-code frame and no release correlation support a code explanation.
2. All hypotheses are refuted or inconclusive after the allowed revisions.
3. The failure cannot be reproduced (unless policy permits an advisory, undeployable suggestion).
4. The only viable patch exceeds the scope policy (for example it needs a migration or lockfile change).
5. A resource budget is exhausted (time, tokens, cost, attempts).
6. A policy violation is detected, such as an attempted forbidden action.

An example of the report Sentinel should write when a database is simply unreachable:

```
Incident INC-2091 · Classification: Infrastructure / dependency availability
Evidence: database connection attempts time out across several services;
          no consistent application exception identifies a code defect.
Candidate code fix: none recommended.
Recommended investigation: database availability, network path, connection
          limits, recent infrastructure changes.
Automated action: incident report created.
Reason for escalation: insufficient evidence to justify a source change.
```

## 10. System overview

The design avoids one giant autonomous agent. It uses explicit workflows, specialised components and enforceable safety boundaries. The orchestration layer, not the model, decides which operations are allowed, what evidence must exist, when retries are permitted and when to stop.

```
REFERENCE APP (logs, metrics, traces, deploy events)
      ↓ OpenTelemetry
INGEST → DETECTOR → INCIDENT MANAGER
                          ↓
               ORCHESTRATOR (state machine)
   ┌─ Code Intelligence (read-only repo index)
   ├─ LLM Gateway (budgets, redaction, logging)
   ├─ Sandbox Broker → isolated runners (reproduce, test patch, verify)
   └─ Knowledge Base (past incidents)
                          ↓
         REPORT → DASHBOARD → HUMAN APPROVAL (signed token)
                          ↓
             DEPLOYER (separate credentials) → staging → canary → rollback
```

**Trust zones.** Zone 0 is untrusted data (logs, repository content, tool output, model output). Zone 1 is the trusted control plane. Zone 2 is the sandbox where hostile code may execute and which holds no secrets. Zone 3 is the deployment plane with the only deployment credentials. The orchestrator cannot reach Zone 3 credentials, and Zone 2 cannot reach anything except its own workspace.

Document 2 specifies each component, its data, its APIs and its failure behaviour.

## 11. Safety, security and governance

| Threat | Control |
| --- | --- |
| Prompt injection through logs, code comments, issue text or tool output | Untrusted-data wrapping, tool permissions set by workflow state rather than by the model, no side-effecting tool reachable on raw untrusted text, injection scenarios in the benchmark |
| Generated code reads secrets or calls the network | No secrets in the sandbox, network disabled during execution, hardened container profile, stronger runtime (gVisor) in V1 |
| Resource abuse (fork bombs, memory, huge output) | CPU, memory, process, time and output limits enforced by the broker |
| Patch games the tests (deleting or weakening tests) | Scope policy, baseline comparison, test-count sanity checks, revert check |
| Fabricated reproduction | Signature-match rule and fail-before, pass-after requirement |
| Model approves its own work | Verification and risk assessment are deterministic, outside the model |
| Approval bypass | Deployer verifies a signed token bound to diff and report hashes; orchestrator holds no signing key |
| Tampering with the record | Append-only, hash-chained audit log; database roles without update or delete rights on it |
| Runaway cost | Per-run token, call and dollar budgets with hard stops |
| Data leakage to model providers | Redaction of secrets and personal data before any model call; sanitised replay payloads only |

**Governance.** Roles: viewer, responder, approver and admin. High-risk patches require a second approver (configurable). Every state change, model call, tool call, approval and deployment is written to the audit trail.

## 12. Reference application and benchmark

### The reference application

A small e-commerce system that behaves like a real one: an order API (FastAPI), an order-processing worker, a payment stub, PostgreSQL and Redis, with structured logs, metrics, traces, automated tests, a CI pipeline and versioned releases. A fault-injection framework introduces a known bug as a real git commit on a release branch, so each incident has a real revision history, real deployment events and real telemetry.

### The benchmark

Forty scenarios across thirteen categories, each with ground truth, hidden tests and expected behaviour:

| Category | Scenarios | Expected outcome |
| --- | --- | --- |
| Input validation (optional field crashes a handler) | 4 | Code patch |
| Regression (a change breaks an existing API) | 4 | Code patch |
| Database (N+1 query, missing index, ORM misuse) | 3 | Two patches; the missing index is a recommendation only |
| Resource management (worker leaks connections) | 3 | Code patch |
| Concurrency (two requests race on one record) | 3 | Code patch |
| Dependency (library upgrade changes behaviour) | 3 | Two patches; a transitive-version conflict is a recommendation only |
| Configuration (incorrect setting) | 3 | Operational report, no patch |
| External dependency (downstream API unavailable) | 3 | Operational report, no patch |
| Background jobs (retries cause duplicate processing) | 3 | Code patch |
| Performance (new code path raises latency) | 3 | Code patch |
| Infrastructure (database unreachable, expired certificate) | 2 | Operational report, no patch |
| Prompt injection (hostile text in logs, code or exceptions) | 3 | Correct diagnosis and patch while ignoring the injected instructions |
| Non-reproducible (timing-dependent, environment-specific) | 3 | Honest escalation, no verified patch |

Thirteen of the forty scenarios (about one third) expect no automatic code patch. Including them is what prevents a system that always patches, or always abstains, from scoring well. Every scenario also contains background noise, and about a quarter contain a deliberate red herring such as an unrelated warning or an innocent concurrent deployment.

The benchmark compares four systems: a rule-based baseline with no model (B0), a single prompt over logs (B1), a model with repository tools but no reproduction or verification gates (B2), and full Sentinel (B3), plus ablations that remove one component at a time.

## 13. Success metrics and initial targets

These targets are working hypotheses to be revised once development runs show what is realistic. The commitment is to report honest results, whatever they are.

| Metric | Definition | Initial target |
| --- | --- | --- |
| Detection precision | Alerts that correspond to genuine incidents | at least 0.90 |
| Detection recall | Genuine incidents detected | at least 0.95 |
| Localisation (top three) | True code location in the top three candidates | at least 85% of code-defect scenarios |
| Root-cause accuracy | Graded 0, 0.5 or 1 against ground truth | mean at least 0.70 on the test split, at least 15 points above B1 |
| Reproduction rate | Reproducible scenarios reproduced with a signature match | at least 60% |
| Patch success | Hidden fail-to-pass and pass-to-pass tests satisfied | at least 50% of fixable scenarios per trial |
| Regression rate | Patches breaking hidden existing tests | at most 5% |
| Safe abstention | Abstains correctly where expected; false abstention reported separately | at least 85% correct, at most 25% false abstention |
| Unsafe-action rate | Injected instruction followed, or forbidden action succeeded | 0 |
| Time to diagnosis | Incident open to cited root-cause report | median at most 10 minutes |
| Cost per incident | Model and compute cost | median at most 2 USD, 90th percentile at most 5 USD |
| Human acceptance | Blind reviewers accept the verified patch | at least 60% |

Patch success and human acceptance are deliberately separate: a patch can pass tests and still be hard to maintain or wrong for the application.

## 14. Scope and release plan

| Tier | Target | Contents |
| --- | --- | --- |
| **MVP** | End of week 14 | Reference application with fault injection; telemetry ingestion; deterministic detection and incident management; code intelligence; the full investigation workflow (triage, hypotheses, reproduction, patch, bounded retry); hardened sandbox; verification engine and risk assessment; signed human approval; dashboard for review; audit log; benchmark of 15 scenarios with baselines B0 to B3 |
| **V1** | End of week 20 | Branch, pull request and CI integration; staging deployment, health checks, canary and rollback; incident knowledge base and ablation; all 40 scenarios; gVisor sandbox tier; security test suite; run replay; published results; documentation, demo video and write-up |
| **Stretch** | Only if time remains | Firecracker microVM tier; a second language adapter (TypeScript); Kubernetes target; learned anomaly detection compared with the rule baseline; learned patch-risk model; Slack or PagerDuty integration; multi-service causal graph |

**Cut line.** If the schedule slips, drop in this order: stretch items, knowledge base ablation, canary percentages (keep single-step staging), then second-wave scenarios that are neither abstention nor injection scenarios. Never drop the sandbox security model, signed approvals, the failure-signature rule or the abstention scenarios.

## 15. Technology summary

The stack is chosen to teach underlying engineering, not to collect fashionable frameworks. A small number of well-tested technologies beats a long list.

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 or newer (pin one minor version) | AI ecosystem, backend work, automation |
| API | FastAPI with Pydantic v2 | Typed APIs and validated structured outputs |
| Orchestration | Explicit state machine persisted in PostgreSQL | Deterministic, resumable, testable (ADR-001) |
| Models | Provider API with structured outputs and tool calling, behind an abstraction; one fast model and one strong model | Routing, cost control, comparison |
| Code search | ripgrep, git, tree-sitter and Python `ast` | Exact, syntax-aware search before embeddings |
| Database | PostgreSQL with pgvector | Incidents, runs, evidence, approvals and case retrieval in one store |
| Queue | PostgreSQL job table with `SKIP LOCKED`, plus a transactional outbox | Durable, one fewer moving part (ADR-003) |
| Cache and pub/sub | Redis | Reference app queue, live dashboard fan-out, rate limits |
| Telemetry | OpenTelemetry SDK and Collector | Standard logs, metrics and traces |
| Sandbox | Hardened Docker, then gVisor (`runsc`) | Tiered isolation (ADR-007) |
| Testing and checks | pytest, Hypothesis, coverage, ruff, mypy, bandit, semgrep, gitleaks, pip-audit | Reproduction, regression and verification gates |
| Dashboard | Next.js with TypeScript | Investigation timeline and approval workflow |
| CI/CD | GitHub Actions | Validation and release automation |
| Deployment | Docker Compose with a reverse proxy for weighted traffic | Enough for staging and canary without Kubernetes |
| Evaluation | Custom harness storing results in PostgreSQL | Reproducible, queryable benchmark |

Not used on purpose: Kubernetes (premature), fine-tuning (not the question), a vector database for everything (exact search is better for exceptions), and a heavyweight agent framework (hides the workflow you are trying to learn).

## 16. Roadmap summary

Estimates assume about 20 to 25 focused hours a week. Each phase ends with a working system and explicit exit criteria. The week-by-week plan and the exact build order are in Document 2.

| Phase | Weeks | Deliverable |
| --- | --- | --- |
| 0. Foundations | 1 | Repository, CI, schemas, contracts, scenario specification format, first architecture decisions |
| 1. Reference app and incident data | 2–3 | Instrumented application, fault injection, first five scenarios, evidence capture |
| 2. Detection and incident management | 4–5 | Ingestion, fingerprinting, detector, incident lifecycle, minimal dashboard list |
| 3. Code intelligence and investigation | 6–8 | Read-only investigation workflow, reports with citations, evaluation harness with B0 and B1 |
| 4. Sandbox, reproduction, patching | 9–12 | Hardened broker, reproduction with signature match, patch generation, bounded retry |
| 5. Verification and approval | 13–14 | Verification gates, risk assessment, signed approvals, review UI. **MVP cut line** |
| 6. Controlled deployment | 15–16 | Pull request, CI, staging, health checks, canary, rollback |
| 7. Learning, evaluation, packaging | 17–20 | Knowledge base, 40-scenario benchmark, ablations, hardening, documentation, demo |
| Buffer | 21–22 | Slippage, polish, write-up |

The source concept estimated 8 to 16 weeks, but its own phase estimates add up to 10 to 19. The plan above is deliberately more conservative because it includes sandbox hardening, signed approvals and a 40-scenario evaluation.

## 17. Skills this project teaches

| Skill | Where it is practised |
| --- | --- |
| LLM APIs, structured outputs, tool calling | Phases 3 to 4 |
| Agent orchestration and state machines | Phases 3 to 5 |
| Context engineering | Phase 3 onward: assembling evidence packs under token budgets |
| Retrieval (exact, syntax-aware, semantic) | Phases 3 and 7 |
| Evaluation, statistics and ablations | Phases 3 to 7 |
| Model selection and routing | Phases 4 and 7 |
| Prompt-injection defence and guardrails | Phases 3 to 7 |
| Backend engineering: APIs, queues, transactions, outbox | Phases 2 to 5 |
| Distributed-systems concerns: idempotency, leases, retries | Phases 2 to 6 |
| Sandboxing and secure execution | Phase 4 and hardening in 7 |
| DevOps: CI/CD, health checks, rollback | Phase 6 |
| Observability of both the target and Sentinel itself | Phases 1 and 3 onward |
| Cost optimisation | Phases 4 and 7 |
| Human-in-the-loop design and cryptographic approvals | Phases 5 to 6 |

**What it will not teach by itself:** model fundamentals, conventional machine learning and deep learning. Add about two hours a week of separate study. Natural small extensions that use Sentinel data: incident classification, anomaly detection compared with the rules, candidate ranking and patch-risk prediction.

## 18. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Scope creep | Project never finishes | Cut line in Section 14; MVP first; phase exit criteria |
| Model non-determinism makes results noisy | Benchmark conclusions unreliable | Pinned model versions, five trials per scenario, bootstrap confidence intervals, recorded cassettes for tests |
| Benchmark too easy or overfitted | Inflated results | Dev/test split, hidden tests, test set run only for release candidates, red herrings, injection and abstention scenarios |
| Fabricated or tautological reproduction | False confidence | Signature-match rule, revert check, human review of the regression test |
| Sandbox escape or credential exposure | Serious security failure | No secrets in sandbox, tiered isolation, security test suite, review by a second person |
| Cost overruns | Stalled evaluation | Per-run budgets, cheap model for classification, caching, early cost measurement |
| Reference app unrealistic | Weak external credibility | Realistic logging, traffic generator, noise, real git history, real release events |
| Flaky tests produce false failures or passes | Wrong verdicts | Repeat runs, flakiness gate, baseline comparison |
| Provider changes or deprecations | Broken prompts | Provider abstraction, pinned versions, prompt versioning and regression suite |
| Looks like a demo, not engineering | Weak interview impact | Publish the benchmark, failures, security model and ADRs |

## 19. Portfolio and interview packaging

### Deliverables checklist

- A repository with a clear architecture overview, reproducible setup (one command to start the reference stack and Sentinel), CI, and tests.
- A recorded incident investigation from detection to approved patch, and a second recording of a correct abstention.
- The benchmark: scenarios, ground truth, harness, raw results, analysis and a candid failure review.
- A security document: threat model, sandbox policy, secret handling, approval design, residual risks.
- ADRs for the major decisions, and a failure-handling document.
- A short technical write-up (about 2,000 words) with the headline results.

### Five-minute demo script

1. Show the healthy reference system and the dashboard.
2. Inject scenario S01 and watch detection, the evidence bundle and the timeline.
3. Follow the hypotheses, including the rejected connection-pool explanation.
4. Show the reproduction failing on the base revision, then passing with the patch.
5. Open the verification matrix, the risk assessment and the diff, and approve.
6. Watch staging, canary and a deliberate unhealthy rollout triggering rollback.
7. Inject an infrastructure outage and show Sentinel correctly declining to write code.
8. Close on the benchmark chart and one honest failure analysis.

### Questions you should be able to answer

- Why an explicit state machine rather than a free-running agent?
- How do you know a reproduction is real, and what stops the model gaming the tests?
- What happens when the model output is malformed, the sandbox crashes or the worker dies mid-run?
- How is prompt injection handled, and what remains exposed?
- What are your baselines, your error bars and your failure categories?
- Why is Docker alone not sufficient, and what does gVisor change?
- How does approval actually bind to the code that is deployed?
- What would you change to support another language or another cloud?

When writing results on a CV, use only measured numbers from the final benchmark, with the scenario count and the baseline comparison.

## 20. Glossary

| Term | Meaning |
| --- | --- |
| Abstention | Sentinel deliberately declines to propose a code change because evidence is insufficient or the cause is not code |
| Base revision | The exact repository commit that was running when the incident occurred |
| Evidence bundle | The frozen set of logs, traces, metrics, commits and records an investigation cites |
| Failure signature | Exception type and originating function used to match a reproduction to a production failure |
| Fingerprint | A stable hash that groups equivalent exceptions |
| Fail-to-pass test | A test that fails on the base revision and passes after the patch |
| Pass-to-pass test | A test that passes before and must still pass after the patch |
| Sandbox broker | The trusted service that runs fixed commands inside isolated runners and attests the results |
| Scope policy | Rules limiting what a patch may touch |
| Trust zone | A boundary class defining who may access what (Zones 0 to 3) |
| Hidden tests | Benchmark tests the system never sees; used only to score patches |
| Outbox | A table written in the same transaction as state changes so events are never lost |

## 21. Review of the original concept: corrections and refinements

The source notes were analysed line by line before this plan was written. The vision is kept intact. These are the places where it was corrected, tightened or extended.

1. **Inconsistent worked example.** The sample error, Cannot read properties of undefined (reading trim), is a JavaScript message attached to Python code. In Python, calling a missing method on a string raises an AttributeError, and `None.strip()` raises an AttributeError mentioning NoneType. Also, a `.trim()` call would fail for every order that includes a note, not only those that omit it, which is a different failure pattern. The plan uses Python-consistent errors and keeps both variants as separate benchmark scenarios (an invalid method and missing-value handling).
2. **Confidence labels.** “High, pending reproduction” is informal. It is replaced by the evidence ladder (Hypothesis, Supported, Reproduced, Verified), defined by actions taken.
3. **Timeline arithmetic.** The stated 8 to 16 weeks does not match the phase estimates, which sum to 10 to 19 weeks. The plan uses a 20-week target with a 14-week MVP and a 2-week buffer.
4. **Isolation.** The notes already say Docker is not automatically a security boundary. This plan makes that concrete: a hardened-Docker tier, a gVisor tier and an optional Firecracker tier, with a written threat model and an escape-attempt test suite.
5. **Model-supplied commands.** The model never supplies shell commands. It supplies files (tests, patches); fixed, allow-listed command templates execute them.
6. **Test tampering.** Added scope policy, baseline comparison, test-count sanity checks and a revert check, so a patch cannot pass by weakening tests.
7. **Fake reproductions.** Added the failure-signature rule so a test failing for an unrelated reason is not accepted.
8. **Approval binding.** The notes require human approval but not how it is enforced. This plan binds approval to the diff hash and report hash with a signed token verified by a separate deployer.
9. **Evaluation methodology.** The notes list metrics but not method. Added hidden tests, dev and test splits, repeated trials, confidence intervals, baselines, ablations and blind human review.
10. **Abstention can be gamed.** A system that always abstains would score well on abstention alone. False abstention on fixable scenarios is reported alongside it.
11. **Queue choice.** A Redis-backed queue can lose jobs on failure. A PostgreSQL job table with an outbox is durable and removes a moving part.
12. **“Safe, autonomous recovery.”** Renamed human-gated recovery. Recommendations for operational incidents are never executed automatically.
13. **Data protection.** Added redaction of secrets and personal data before any model call and the use of sanitised replay payloads.
14. **Audit integrity.** Added a hash-chained, append-only audit log.
15. **Scope.** Added an explicit Python-only MVP, a cut line and a list of non-goals so the project stays finishable.

## 22. Decisions made and open questions

### Decisions already taken

| ID | Decision | Detail |
| --- | --- | --- |
| ADR-001 | Explicit state machine, not a framework-driven agent | Document 2, Section 8 |
| ADR-002 | Modular monolith with hard module boundaries; security-critical parts run as separate processes | Document 2, Section 3 |
| ADR-003 | PostgreSQL job queue and transactional outbox | Document 2, Section 4 |
| ADR-004 | Deterministic detection before any learned detection | Document 2, Section 6 |
| ADR-005 | Exact and syntax-aware search first; embeddings for the knowledge base and as a fallback | Document 2, Section 7 |
| ADR-006 | Signed approvals verified by the deployer | Document 2, Section 11 |
| ADR-007 | Tiered sandbox: hardened Docker, gVisor, optional Firecracker | Document 2, Section 15 |
| ADR-008 | The model supplies files, never commands | Document 2, Section 9 |
| ADR-009 | Python-only targets in MVP and V1 | Document 2, Section 1 |
| ADR-010 | Telemetry stored in PostgreSQL for the prototype | Document 2, Section 5 |
| ADR-011 | Hidden tests and held-out test split | Document 2, Section 18 |
| ADR-012 | Evidence ladder instead of numeric confidence | Document 2, Section 8 |

### Questions for you

1. Which model provider and models will you use, and what is your monthly spending cap? A full evaluation (about 800 runs across variants) plausibly costs several hundred US dollars; measure early and adjust.
2. How many hours a week can you actually give? The schedule scales linearly with this.
3. Do you have a Linux machine or VM? gVisor requires Linux; on macOS or Windows use a Linux VM or a small cloud instance for the sandbox host.
4. Will the repository be public, and under what licence?
5. Can you recruit two or three engineers for a blind patch review in Phase 7?
6. Do you want the dashboard built with Next.js (more portfolio value) or a lighter server-rendered interface (faster)?
