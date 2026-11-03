# ADR-0016: Open end-to-end benchmark

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-29, G8

## Context
Existing public benchmarks cover parts of the problem, mostly root-cause analysis. None scores the full chain together: detection → root cause → signature-matched reproduction → patch → verification → abstention, with hidden tests and scenarios where no patch is the right answer.

## Decision
Publish the scenario format (`design/06-schemas.md` §C.2), the harness, the scoring code, all 40 scenarios and the raw per-trial results.
- **Hidden tests for the test split** are released only after the final campaign, or kept server-side for a submission service (decided in Phase 7).
- Licences: code Apache-2.0; scenario data CC-BY-4.0 (proposed, OI-08).
- Results are always reported as correct/false abstention pairs and risk-coverage curves (D2 §18.6).

## Consequences
- **Positive:**
  - External credibility.
  - Comparable results across systems.
  - Room for community-contributed scenarios.
- **Negative:**
  - Contamination risk once published. Mitigation: versioned scenario sets and a held-back test split.

## Acceptance
AT-EVAL-01 to AT-EVAL-06; the B57a packaging checklist.
