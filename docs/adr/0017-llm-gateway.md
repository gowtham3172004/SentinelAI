# ADR-0017: Provider-independent LLM gateway; Groq free tier first

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-23, FR-25, NFR-05, NFR-06, NFR-08

## Context
- The owner has no paid model access.
- Groq offers free, OpenAI-compatible access to open-weight models, with per-minute and per-day caps and frequent model churn.
- Independent evaluation needs a second model family.

## Decision
- A single `openai_compatible` adapter built on httpx (no vendor SDKs).
- A model registry with startup capability probes.
- Purpose → tier → candidate routing, with per-run model pinning and recorded failover.
- A rate-limit-aware scheduler that **parks** runs instead of failing them on quota.
- Provider-aware context budgets.
- An egress policy by data class.
- Token and request budgets alongside USD.
- A second provider from a different model family for the judge.
- Specification: `design/01-architecture.md` §6.

## Consequences
- **Positive:**
  - Zero-cost start.
  - Any OpenAI-compatible or local provider plugs in.
  - Reproducibility is tracked.
- **Negative:**
  - Free-tier throughput limits campaign speed (RK-01).
  - Open models may patch worse than frontier models (RK-03).
  - Evidence packs may have to shrink to fit per-minute token limits; this is recorded as a limitation.

## Alternatives considered
- Vendor SDKs: rejected for lock-in and opaque retries.
- A LiteLLM proxy: rejected for now; it adds a service, and our needs are narrow and security-sensitive.

## Acceptance
AT-LLM-01 to AT-LLM-09.
