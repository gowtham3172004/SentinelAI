# 06. Schemas: Appendices A, B and C

These are the appendices D2 refers to but never included (finding A1). The structured outputs are specified as Pydantic models (implementation: `sentinelai.domain.schemas`). Free-text fields are only `narrative`, `rationale` and `notes`, as D2 §8.8 requires, and each has a length cap.

## Appendix A: structured model outputs

```python
# Common
EvidenceId = Annotated[str, StringConstraints(pattern=r"^E-\d{1,4}$")]
Rationale = Annotated[str, StringConstraints(max_length=1200)]
Narrative = Annotated[str, StringConstraints(max_length=2000)]


class Citation(BaseModel):
    evidence_id: EvidenceId
    stance: Literal["supports", "contradicts", "context"]


# A.1 TRIAGE (fast tier)
class TriageOutput(BaseModel):
    incident_class: IncidentClass  # enum from D2 §13.1
    agrees_with_prior: bool
    override_citations: list[Citation] = []  # required (>=1) when agrees_with_prior is False
    signature_kind: Literal["exception", "status_code", "duplicate_effect", "metric_threshold"]
    rationale: Rationale


# A.2 CORRELATE
class EvidenceRequest(BaseModel):
    tool: Literal[
        "query_logs",
        "get_trace",
        "get_metric_series",
        "get_timeline",
        "list_releases",
        "get_config_diff",
        "search_text",
        "find_symbol",
        "get_file",
        "get_function",
        "callers",
        "callees",
        "route_handler",
        "git_log",
        "git_diff",
        "diff_between",
        "git_blame",
        "tests_for",
        "contract_for",
        "dependency_diff",
        "kb_search",
    ]
    args: dict[str, JsonScalar | list[JsonScalar]]  # validated by ToolPolicy per tool
    reason: Rationale


class CorrelateOutput(BaseModel):
    requests: list[EvidenceRequest] = Field(max_length=10)
    summaries: list[tuple[EvidenceId, Annotated[str, StringConstraints(max_length=400)]]] = []


# A.3 HYPOTHESIZE (strong tier)
class CodeLocationRef(BaseModel):
    path: str
    symbol: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class Hypothesis(BaseModel):
    key: Annotated[str, StringConstraints(pattern=r"^H[1-9]$")]
    statement: Annotated[str, StringConstraints(max_length=400)]
    mechanism: Annotated[str, StringConstraints(max_length=800)]
    incident_class: IncidentClass
    remediation: RemediationCategory
    is_non_code_alternative: bool
    citations: list[Citation] = Field(min_length=1)
    locations: list[CodeLocationRef] = []
    rationale: Rationale


class PredictionQuery(BaseModel):  # discriminated union of the predicates in 03 §6.2
    key: Annotated[str, StringConstraints(pattern=r"^Q[0-9]{1,2}$")]
    query: PredicateAST


class Expectation(BaseModel):
    hypothesis_key: str
    query_key: str
    expected: Literal["true", "false", "indifferent"]


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis] = Field(min_length=2, max_length=5)
    queries: list[PredictionQuery] = Field(max_length=12)
    expectations: list[Expectation]
    # model validators: >=1 is_non_code_alternative; every (hypothesis, query) pair has an expectation


# A.4 VALIDATE (strong tier, query planning only)
class FalsificationPlan(BaseModel):
    additional_queries: list[PredictionQuery] = Field(max_length=6)
    additional_expectations: list[Expectation]
    evidence_needed: list[EvidenceRequest] = Field(max_length=5)  # drives VALIDATE -> CORRELATE
    rationale: Rationale


# A.5 REPRODUCE (strong tier)
class ReproductionProposal(BaseModel):
    strategy: Literal["S1", "S2", "S3", "S4"]
    test_path: Annotated[str, StringConstraints(pattern=r"^tests/sentinel/test_[a-z0-9_]+\.py$")]
    test_content: Annotated[
        str, StringConstraints(max_length=8000)
    ]  # <= 80 lines enforced separately
    asserts_contract: Annotated[str, StringConstraints(max_length=400)]
    expected_signature: dict[str, str]  # e.g. {"exception_type": "...", "function": "..."}
    not_reproducible_reason: (
        Literal["external_service", "production_data", "hardware_timing"] | None
    ) = None
    rationale: Rationale


# A.6 PATCH (strong tier)
class PatchProposal(BaseModel):
    unified_diff: Annotated[str, StringConstraints(max_length=20000)]
    rationale: Rationale
    expected_behavior: Annotated[str, StringConstraints(max_length=800)]
    side_effects: Annotated[str, StringConstraints(max_length=800)]


# A.7 DIAGNOSE_FAILURE (strong tier)
class FailureDiagnosis(BaseModel):
    cause: Literal["patch", "hypothesis", "environment", "flaky", "baseline_failure"]
    citations: list[Citation] = []
    next_action: Literal["PATCH", "HYPOTHESIZE", "REPORT"]
    notes: Annotated[str, StringConstraints(max_length=1200)]


# A.8 REPORT narrative (fast tier)
class ReportNarrative(BaseModel):
    summary: Narrative
    impact: Narrative
    root_cause_explanation: Narrative
    recommended_action: Narrative
    citations: list[Citation] = Field(min_length=1)


# A.9 Evaluation judge (independent provider, temperature 0)
class JudgeVerdict(BaseModel):
    key_facts: list[tuple[str, Literal["covered", "partially", "missing"]]]
    notes: Annotated[str, StringConstraints(max_length=1200)]
```

## Appendix B: protocol schemas

### B.1 Broker execution request (orchestrator → broker)

```json
{
  "idempotency_key": "<run_id>:<step_seq>:reproduce:1",
  "tenant_id": "<uuid>", "run_id": "<uuid>",
  "template": "pytest_file",
  "parameters": {"test_file": "tests/sentinel/test_inc1042_note_none.py"},
  "workspace": {
    "base_sha": "<40-hex>",
    "files": [{"path": "tests/sentinel/test_inc1042_note_none.py", "sha256": "<hex>", "blob": "blob://<hex>"}],
    "diff": null
  },
  "repeat": 3
}
```

### B.2 Approval token claims

D2 §11.2, plus:

| Claim | Meaning |
|---|---|
| `ten` | Tenant ID |
| `vrs` | SHA-256 of the verification report |
| `rsk_src` | Always `"api_recomputed"` |
| `wa` (V1) | List of `{credential_id, authenticator_data, client_data_json, signature}`, one per approver |

### B.3 Proof link statement (ADR-014)

Each link is a DSSE envelope with `payloadType: application/vnd.in-toto+json` carrying an in-toto Statement v1:

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{"name": "patch:PATCH-12", "digest": {"sha256": "<diff sha256>"}}],
  "predicateType": "https://sentinelai.example/attestations/incident-remediation/<link>/v1",
  "predicate": {
    "tenant": "<uuid>", "incident": "INC-1042", "run": "RUN-7",
    "prev_link_sha256": "<hex|null>",
    "investigation": {"evidence_manifest_sha256": "...", "base_sha": "...",
                      "leading_hypothesis": {"key": "H1", "level": "reproduced"},
                      "prediction_results_sha256": "...",
                      "reproduction_executions": [{"id": "...", "attestation_sha256": "..."}],
                      "workflow_version": "...", "prompt_set_version": "...", "models": {"strong": "..."}},
    "verification": {"verification_run": "...", "status": "verified", "report_sha256": "...",
                     "gates": {"G01": "pass", "G02": "pass"}, "image_digest": "sha256:..."},
    "approval": {"approvals": [{"user": "...", "decision": "approve", "risk_recomputed": "low",
                               "passkey": {"credential_id": "...", "verified": true}}],
                 "token_jti": "..."},
    "delivery": {"mode": "pr_only|managed", "pr_url": "...", "merged_sha": "...",
                 "image_digest": "sha256:...", "outcome": "succeeded|rolled_back"}
  }
}
```

- Only the section for the link's own type is present in each statement.
- The DSSE signature is Ed25519 over the PAE (pre-authentication encoding), with `keyid` = the signer's `kid`.
- The `predicateType` URI is a placeholder until the project domain or repository URL is fixed (open issue OI-06).
- `sentinelai proof verify bundle.json` checks: every envelope's signature against the embedded JWKS, signer role ↔ link type, the `prev_link_sha256` chain, and subject digest equality.

### B.4 Prediction AST example

```json
{"key": "Q1", "query": {"predicate": "precedes",
  "a": {"kind": "fingerprint", "id": "fp_3f9a1c2d4e5b6a7c"},
  "b": {"kind": "metric_onset", "id": "db.pool.wait_ms"}, "min_gap_s": 0}}
```

### B.5 Tenant data policy and quotas

```yaml
data_policy:
  egress:                     # data class -> providers allowed to receive it
    P1: [groq, gemini]
    P2: [groq]                # source code, log text
  allow_providers_that_train_on_inputs: false
  retention_days: {logs: 7, spans: 7, windows: 30, llm_payloads: 30}
  incident_records: indefinite
quotas:
  active_runs: 3
  model_tokens_per_day: 1500000
  model_requests_per_day: 2000
  sandbox_executions_per_day: 400
  storage_gb: 20
```

## Appendix C: configuration files

### C.1 `sentinel.yaml` (in the target repository)

```yaml
version: 1
service: order-api
source_roots: [shop/]
tests_root: tests/
regression_test_dir: tests/sentinel/
endpoints:
  - {method: POST, path: /api/orders, tier: 0}
  - {method: GET,  path: /api/orders, tier: 1}
sensitive_modules: ["shop/payments/**", "shop/auth/**"]
commands:                      # overrides of language-adapter defaults; template IDs only
  type_checker: mypy           # or: none
scope_policy:                  # may only tighten D2 §9.6 defaults
  max_files_changed: 3
  max_changed_lines: 60
  extra_forbidden_paths: ["shop/migrations/**"]
gates:
  required: [G06, G07, G08, G09, G10, G12]   # non-blocking gates promoted to required
delivery:
  mode: pr_only                # or: managed
  monitoring_window_min: 30
```

### C.2 `scenario.yaml` (in the benchmark)

```yaml
id: S01
version: 1
title: Optional customer note is None
category: input_validation
class: code_defect
wave: 1
split: dev
expected_outcome: proposal_ready     # or report_only | escalated_* (D2 §8.5)
signature_kind: exception
reproducible: true                   # false => NOT_REPRODUCIBLE_HERE expected
abstention_expected: false
fault:
  patch: fault.patch
  commit_message: "Normalise customer notes"   # neutral; must not reveal the fault
  release: v1.8.4
traffic: traffic.yaml
red_herrings: [db_pool_warning_after_onset]
injection: null                      # or {file: injection.txt, location: log|code_comment|exception}
ground_truth_ref: bench_truth://S01  # resolved only by the harness
```

### C.3 Provider registry entry (platform configuration)

```yaml
providers:
  groq:
    adapter: openai_compatible
    base_url: https://api.groq.com/openai/v1
    key_ref: secret://providers/groq
    terms: {trains_on_inputs: unknown, retention: unknown, region: unknown, reviewed_at: null}  # filled in P0-12
models:
  - id: groq/llama-3.1-8b-instant
    family: llama
    tier: fast
    context_window: <probe>
    capabilities: {json_schema: <probe>, json_object: <probe>, tools: <probe>}
    limits: {rpm: <probe>, tpm: <probe>, rpd: <probe>, tpd: <probe>}
    price: {in_per_mtok: 0, out_per_mtok: 0}
```
