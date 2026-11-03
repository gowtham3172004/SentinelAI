# ADR-0021: Delivery modes (PR-only and managed)

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-15, FR-31

## Context
Real adopters already run their own CD to production. Sentinel driving production canaries would conflict with it. The reference environment still needs the full staged pipeline.

## Decision
- **PR-only** (the default for adopters):
  - Branch and PR carrying the report and proof bundle.
  - CI result recorded.
  - Post-deploy watch triggered by the release event whose commit contains the patch: resolve when the fingerprint stays absent; reopen and recommend rollback if it recurs.
- **Managed:** D2 §11 in full (staging, promotion approval, canary 5/25/100, automatic rollback).
- In both modes:
  - The image digest is bound to its commit.
  - The patch from merge-base to PR head must equal the approved diff.

## Consequences
- **Positive:**
  - Adoptable without taking over the tenant's CD.
  - FR-15 fully kept, in managed mode.
- **Negative:** two delivery paths to test.

## Alternatives considered
- Managed mode only: rejected; adopters can't use it.
- PR-only mode only: rejected; it would weaken FR-15.

## Acceptance
AT-DLV-01 to AT-DLV-07.
