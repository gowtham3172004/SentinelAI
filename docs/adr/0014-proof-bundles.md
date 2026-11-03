# ADR-0014: Cryptographic proof bundles

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-27, NFR-04, INV-11

## Context
Approval binds to diff and report hashes, but nothing lets a third party check, offline, that deployed bytes trace back through verification and reproduction to the incident evidence. Any single component that signs everything is also a single point of forgery.

## Decision
Each remediation produces up to four **links**: investigation, verification, approval and delivery.
- Each link is an in-toto Statement v1 inside a DSSE envelope.
- Each is **signed by the component that has authority over that link**: orchestrator, broker, API or deployer.
- Links are chained by `prev_link_sha256`.
- Stored in the insert-only `proof_links` table and exported with the JWKS.
- `sentinelai proof verify` checks the signatures, signer role ↔ link type, the chain and the subject digests.
- Specification: `design/06-schemas.md` §B.3.

## Consequences
- **Positive:**
  - Tampering by any single component is detectable.
  - Bundles attach to PRs.
  - Aligns with software supply-chain attestation practice.
- **Negative:**
  - Four signing keys to rotate (NFR-19).
  - The DSSE pre-authentication encoding is implemented in-house (small and spec-tested).

## Alternatives considered
- A single orchestrator-signed report: rejected as a single point of forgery.
- Sigstore keyless signing: deferred, because it needs online infrastructure; it may be used later for release images.

## Acceptance
AT-PRF-01 to AT-PRF-03, AT-OPS-07.
