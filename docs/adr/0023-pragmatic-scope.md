# ADR-0023: Pragmatic scope ("ready" over enterprise breadth)

- Status: Accepted (owner decision OD-16, 2026-11-03)
- Requirements affected: FR-21, FR-27, FR-28, FR-30, FR-31, NFR-11, NFR-15, NFR-19 (see the table)
- Amends: ADR-014, ADR-015, ADR-019, ADR-021, design/07, design/09

## Context
The consolidated specification (v1.1) added enterprise breadth: multi-tenant administration, per-link multi-signer attestations, passkey consent, managed canary delivery, point-in-time recovery and key escrow. The owner directed that Sentinel AI should become usable end to end first, keeping only the infrastructure that materially improves safety, evidence quality or usability.

## Decision
Keep every **safety-critical** control. Replace enterprise-grade mechanisms with simpler ones that still demonstrate each requirement. Defer the rest, explicitly and traceably.

**Kept unchanged:**
- Sandbox isolation: hardened Docker, with gVisor or a cloud VM before any hostile code runs.
- Signed human approval bound to the diff and report hashes.
- The failure-signature rule, broker-side signature recomputation and the verification gates.
- The audit hash chain and its tamper tests.
- Fail-closed basic tenant isolation (already built).
- The prediction engine.
- Abstention and honest outcomes.
- Credential redaction.
- Docker Compose (no Kubernetes).

| Area | v1.1 specification | Now (MVP) | Deferred (tracked) | Direction |
|---|---|---|---|---|
| Tenancy (FR-21) | Pooled RLS + multi-tenant admin, quotas UI, erasure, envelope encryption, silo automation | **Basic isolation as built in migration 0001**: one active tenant, RLS fail-closed, tenant-scoped tables | Tenant admin, erasure, envelope encryption, per-tenant quotas UI | W (owner-approved deferral) |
| Proof (FR-27, ADR-014) | Four DSSE links, each signed by its own component | **One evidence bundle per remediation**: JSON manifest of hashes (evidence, reproduction executions, diff, verification report, approval) with one Ed25519 signature, plus `sentinelai proof verify` | Per-link multi-signer DSSE / in-toto | W |
| Human approval (FR-14, FR-28, ADR-015) | Passkey assertion verified by the deployer | **Signed approval token** (Ed25519 JWS bound to diff, report and verification hashes; single-use; recent re-authentication; HIGH risk needs 2 approvers) | Passkeys | W (FR-14 itself fully met) |
| Delivery (FR-15, FR-31, ADR-021) | PR-only plus managed staging, canary 5/25/100, automatic rollback | **PR-only delivery** (branch, PR with report and evidence bundle, CI status) and a **post-deploy watch** that reopens on recurrence | Managed staging, canary and automatic rollback | W (FR-15 canary/rollback deferred) |
| Backup (NFR-11) | MVP nightly dump; V1 PITR + key escrow | **Backup and restore commands** (`pg_dump` + blobs) with an automated restore test | PITR, key escrow | W |
| Notifications (FR-30) | Channels table, retries, Slack format | **One required webhook** for incident opened, proposal ready and run escalated, with signed payload and bounded retry | Multiple channels and formats | C |
| Self-monitoring (NFR-15) | External dead-man switch plus local watchdog | Health and readiness endpoints, and `sentinelai doctor` | Dead-man switch | W |
| Key rotation (NFR-19) | Zero-downtime rotation drill | `kid` in every token and signature, so rotation is possible | Rotation drill | W |
| Sandbox (ADR-007) | Tier 1 + Tier 2 + optional microVM | Hardened Docker profile; gVisor **or** cloud VM required before hostile workloads; escape-test suite | Firecracker tier | unchanged |
| Benchmark (ADR-016) | 40 scenarios, ablations, public release | A small scenario set (target 8–10 across the main classes, including non-code and injection cases), baselines B0–B3, honest metrics | The full 40, ablations, public packaging | W |

## Consequences
- **Positive:** a much shorter path to a working, demonstrable product. All safety properties stay.
- **Negative:**
  - The proof is weaker against a compromised signing component than per-link signing would be.
  - Approval relies on the API's signing key; a compromised API remains a residual risk (design/04 §4).
  - Without managed delivery, rollback is a recommendation, not automated.
- **Traceability:** each deferred item stays in design/12 marked "Deferred (ADR-023)" and can be picked up later without redesign. The schemas and token formats reserve the needed fields (`wa` passkey claim, `kid`).
