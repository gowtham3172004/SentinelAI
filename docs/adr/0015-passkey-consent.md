# ADR-0015: Passkey (WebAuthn) human consent verified by the deployer

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-14, FR-28, INV-6; extends ADR-006

## Context
D2 mints approval tokens inside sentinel-api with the API's own key, so a compromised API can manufacture "human" approvals. FR-14 asks for *signed human approval*.

## Decision
- Every approval decision carries a WebAuthn assertion (`userVerification=required`). Its challenge is the SHA-256 of the canonical decision tuple: tenant, patch hash, report hash, verification report hash, scope, environment, base SHA, token jti and recomputed risk.
- The **deployer verifies each assertion itself**:
  - It recomputes the challenge from its own database reads.
  - It accepts only credentials registered at least 24 hours earlier and not revoked.
- Registration requires an existing passkey or a two-admin recovery, and is audited and notified.
- **MVP:** the D2 JWS token, with the `wa` claim reserved. **V1:** passkeys enforced.
- Specification: `design/04-security-and-threat-model.md` §6.

## Consequences
- **Positive:** consent becomes the human's own cryptographic act. API compromise alone can't produce a deployable approval.
- **Negative:**
  - Device dependency.
  - A recovery runbook is needed (RB-07).
  - A browser ceremony in the dashboard.

## Alternatives considered
- TOTP step-up: rejected, because the API can still mint tokens.
- Hardware signing keys only: rejected for friction.

## Acceptance
AT-APR-04, AT-APR-01.
