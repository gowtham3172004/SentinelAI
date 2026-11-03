# ADR-0013: Prediction engine (executable falsification)

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-08, FR-26, G2; amends ADR-012

## Context
D2 asks for "predicted observations" per hypothesis, as free text. The Supported level counts cited evidence kinds, which a model can satisfy by citing plausible items without ever risking being wrong.

## Decision
- Hypotheses come with a **prediction matrix**: typed JSON queries plus a per-hypothesis expectation (true / false / indifferent).
- The engine adds a **system battery** whose expectations come from a versioned class expectation table, never from the model.
- It evaluates every query deterministically over the frozen bundle.
- **Supported** additionally requires at least one confirmed discriminating battery prediction and no refutation.
- Full specification: `design/03-workflow-and-predictions.md` §6.

## Consequences
- **Positive:**
  - Supported means a test that could have failed didn't.
  - The rival-hypothesis tests of D2 §7.7 generalise into one mechanism.
  - More model judgement is replaced by system computation.
- **Negative:**
  - Possible rise in false abstention (RK-10).
  - The class table needs tuning on the dev split only.
- **No new external dependency:** Pydantic models plus SQL over existing tables.

## Alternatives considered
- Free-text predictions judged by the model: rejected as model self-assessment.
- Bayesian scoring: rejected; it needs calibrated priors we don't have.

## Acceptance
AT-PRD-01 to AT-PRD-04, AT-LVL-01.
