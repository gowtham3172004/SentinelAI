# Spike P0-12: model provider feasibility

Date of live probe: 2026-11-03 · Machine: Windows 11 dev workstation · Tool: `sentinelai providers probe` (v0.1.0)
Raw reports: `out/*-probe.json` (git-ignored, regenerable). Keys were read from the local configuration and never printed. The reports were checked for credential patterns: 0 matches.

> **A successful probe is not evidence of model quality.** It shows only that the endpoint was reachable, the key was accepted, the model served a request, and the model returned schema-valid JSON for a trivial prompt ("What is 2 + 3?"). Fitness for triage, hypotheses, reproduction or patching is measured **only** by the benchmark (`docs/design/11`, AT-EVAL-*). No model is selected for any tier on the basis of this spike alone.

## 1. What is verified, and how

"Verified" means observed against the real provider on the date above, with the evidence named.

| Capability | Groq | Gemini | Evidence |
|---|---|---|---|
| Endpoint reachable over HTTPS | **Verified** | **Verified** | Probe report `reachable: true` |
| API key accepted | **Verified** | **Verified** | `authenticated: true` |
| Model listing (`/models`) | **Verified** (11 models) | **Verified** (60 models; IDs carry a `models/` prefix) | `listed_models` |
| `json_schema` response format accepted, valid output | **Verified**: gpt-oss-20b, gpt-oss-120b, qwen3.8-27b | **Verified**: gemini-3.5-flash-lite, gemini-3.6-flash | `json_schema.supported/valid_output` |
| `json_object` response format accepted, valid output | **Verified**: same three models | **Verified**: same two models | `json_object.*` |
| Rate-limit headers | **Verified**: `x-ratelimit-*` present | **Not provided**: no rate-limit headers | `rate_limit` |
| End-to-end structured round trip through `complete_structured` | **Verified** (live test) | **Verified** (live test) | `SENTINELAI_LIVE_TESTS=1 uv run pytest -m live`: 2 passed |
| Credentials absent from outputs, errors, logs, tracebacks | **Verified by deterministic tests** against a hostile local provider that echoes the key everywhere | same | `tests/integration/test_credential_exposure.py` (AT-LLM-04) |

### Measured limits (Groq, gpt-oss models)

Read from response headers. These are limits for this account and tier, and they may change.

| Limit | Value | Interpretation |
|---|---|---|
| Requests | 1,000, reset ≈ 173–346 s for one used request | Per-day request budget |
| Tokens | **8,000**, reset ≈ 4–5 s | **Per-minute token budget** |

Tokens used per probe call: 126–189 in and 54–110 out (gpt-oss spends output tokens on hidden reasoning). Gemini: 26 in and 6 out.

## 2. What is not verified

| Item | Status | Why it matters | When it gets verified |
|---|---|---|---|
| **Model quality** for any Sentinel purpose | **Unverified** | The only thing that matters for tier selection | Benchmark campaigns, Phases 3–5 |
| Behaviour on realistic prompt sizes (8k–24k tokens) | Unverified | **Groq's 8,000 TPM means no single request may exceed ~8k tokens including output.** D2 §8.9 budgets (24k for hypotheses) can't fit as written on this tier. | Gateway provider-aware budgets (B25a) + first investigation runs |
| Daily token caps (`tpd`) on Groq | Unverified | Headers expose the per-minute token limit, not a daily token cap | Measured during Phase 3 runs |
| Gemini rate limits | Unverified | Gemini sends no rate-limit headers, so the scheduler must use configured limits and 429 handling | Configure from Gemini docs; observe 429 behaviour |
| Gemini availability under load | **Unstable** | gemini-3.5-flash returned HTTP 503 ("high demand"); gemini-3.8-flash timed out at 60 s | Retry and park logic (B25a); re-probe |
| Tool calling | Unverified | Not used by the current design for model output (structured outputs only) | When/if needed |
| Determinism at temperature 0 | Unverified | Replay relies on recorded responses, not on determinism, but variance affects benchmark noise | Benchmark trials |
| Provider data terms (training, retention) | **Unverified: recorded as `unknown`** | Real tenant data must not be sent until reviewed (01 §5, RK-12) | Manual review before the first real tenant |
| OpenRouter, Cerebras, Mistral, Ollama, LM Studio | Unverified (no keys or local servers) | Same adapter, so they're expected to work, but that's unproven | When configured |

## 3. Findings that changed the code

These came from live calls; none was detectable with fakes.

1. **Catalogue ≠ callable.** Gemini lists `gemini-2.5-flash` and `gemini-2.5-flash-lite` but returns HTTP 404 ("no longer available to new users"). Groq's 404 for four suggested models matched its listing.
   - The probe now reports `listed` and `callable` separately.
   - The live test tries candidates in order instead of trusting the catalogue.
2. **Gemini prefixes model IDs** with `models/` in `/models` but expects bare names on chat calls. Added `ProviderSpec.model_id_prefix` (set for Gemini), with a unit test.
3. **Suggested models updated** to those that were callable today. Each entry carries the date of its last probe.

Separately, the deterministic credential-exposure suite found two exposure channels. Both are now fixed and tested:
- At DEBUG level, `httpcore` logs response headers verbatim. Fixed with process-wide log-record redaction of registered secrets.
- Typer's default tracebacks print local variables, which would include the request headers. Fixed with `pretty_exceptions_show_locals=False`.

## 4. Implications for the plan

- **RK-01 confirmed:** the free-tier per-minute token ceiling (8k on Groq's gpt-oss models) is the binding constraint, more than daily requests. Provider-aware context budgets (01 §6.1) are **mandatory**, and evidence packs will be smaller than D2's initial budgets. Every dropped section is recorded as a limitation.
- **OI-02 (strong tier):** candidates are `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` on Groq. **Not decided.** That needs benchmark evidence.
- **OI-03 (independent judge):** a different family is available and callable (Gemini 3.x flash models, versus gpt-oss and qwen on Groq). **Not decided.** That needs the calibration in AT-EVAL-04.
- Gemini's availability problems mean judge calls must tolerate 503 and timeouts (retry, then park). Benchmark campaigns must not treat provider unavailability as a model failure.

## 5. How to reproduce

```sh
uv run sentinelai providers probe groq --out out/groq-probe.json
uv run sentinelai providers probe gemini -m gemini-3.5-flash-lite -m gemini-3.6-flash --out out/gemini-probe.json
SENTINELAI_LIVE_TESTS=1 uv run pytest -m live        # PowerShell: $env:SENTINELAI_LIVE_TESTS="1"; uv run pytest -m live
```
