# 04. Security boundaries and threat model

Amends D2 §15–§16. All D2 controls stay in force. This document adds tenancy, privacy, provider and component-compromise analysis, and fixes C1 to C9.

## 1. Assets

| Asset | Why it matters |
|---|---|
| Tenant source code, logs, traces | Confidential tenant IP and data (P2) |
| Secrets and personal data in telemetry | P3; must never be stored or sent |
| Signing keys: approval JWS (API), attestation (broker), proof link keys (orchestrator, broker, API, deployer), release HMAC secrets | They anchor every trust decision |
| Deployment and git credentials (deployer, per tenant) | Direct production reach |
| Model provider keys | Cost, quota, data egress |
| Integrity of gate results, risk levels, approvals and the audit chain | The product's core promise |
| Users' passkeys | Human consent |
| Sandbox host | Shared execution substrate |

## 2. Adversaries

D2 §15.1 A1 to A4 still apply.

| ID | Adversary | Example |
|---|---|---|
| A1 | Hostile input that steers the model | Instruction-like text in logs, code comments or exception messages |
| A2 | Faulty model | Destructive or test-gaming patch; looping |
| A3 | Supply chain | Malicious package in the target lockfile or the Sentinel lockfile |
| A4 | Malicious or careless insider | Approving around policy |
| **A5** | Malicious or curious tenant user | Reading another tenant's incidents; exhausting shared quotas |
| **A6** | Model provider (data exposure or a malicious response) | Retains or trains on code; returns crafted output |
| **A7** | One compromised Sentinel component | Code execution inside orchestrator, API, broker or deployer |
| **A8** | Network attacker | Replaying webhooks; intercepting traffic between services |

## 3. Trust boundaries

| ID | Boundary | Crossing | Primary controls | Tests |
|---|---|---|---|---|
| TB-1 | Target → telemetry | OTLP, release webhook, alerts | Per-collector token; HMAC + timestamp + idempotency key (replay window 5 min); schema validation; redaction; size caps | AT-ING-03/04, AT-REL-EV-01 |
| TB-2 | Control plane → LLM provider | Prompts out, completions in | Egress allow-list; redaction layer 2; egress policy by data class; output schema validation; untrusted wrapping of all evidence | AT-LLM-04/05, AT-SEC-02 |
| TB-3 | Orchestrator → broker | Execution requests | Service token (5 min, per-service key); template ID + validated parameters only; content by hash | AT-BRK-04, AT-SEC-03 |
| TB-4 | Broker → runner | Workspace tar in, `/out` back | Tar validator; hardened config from one constructor; no network or secrets; output caps; hardened parsers | AT-BRK-05, AT-SBX-* |
| TB-5 | API → deployer | Approval token (+ passkey assertion) | Ed25519 JWS; single-use jti; hash binding; passkey verified by the deployer itself; base_sha check | AT-APR-01/04 |
| TB-6 | Browser → API | Sessions, decisions | OIDC; HTTP-only SameSite cookies; CSRF token; re-authentication window; passkey step-up for decisions (V1); CSP | AT-UI-03, AT-APR-03 |
| TB-7 | Tenant ↔ tenant | All data and compute | RLS fails closed; tenant labels; quotas; per-tenant secrets; no cross-tenant knowledge base | AT-TEN-01 to 05 |
| TB-8 | Deployer → git host / CI / targets | Branches, PRs, deploys | Credentials limited to the `sentinel/` prefix; never merges; image digest pinned | AT-DLV-01/02 |
| TB-9 | Harness ↔ Sentinel | Ground truth, hidden tests | Separate role, schema, filesystem and network; neutral commit messages; clean repository per trial | AT-EVAL-02/03 |
| TB-10 | Sandbox host ↔ developer workstation | WSL2 VM boundary (spike phase) | §5 decision rules | P0-11 report |

## 4. What a compromised component can and can't do (A7)

This is the reason for the separation of authority.

| Compromised | **Can** | **Can't** (and why) |
|---|---|---|
| Orchestrator | Write misleading hypotheses and reports; propose bad patches; burn its tenant's quota | Forge executions or gate results (no grant; broker signature). Lower risk to avoid a second approver (API recomputes, C2). Forge G01 (broker recomputes the signature, C1). Mint approvals. Deploy. Read other tenants' data (RLS). |
| API | Show users false data; write approval rows; mint JWS tokens | **Deploy without a human passkey assertion** (V1: the deployer verifies the WebAuthn signature against a credential registered more than 24 h earlier; registration is audited and notified). Forge verification. |
| Broker | Forge execution results for patches | Approve or deploy. **Mitigations:** independent CI on the PR, human diff review, and the counterfactual gate re-run by CI (V1). Documented as a residual risk. |
| Deployer | Deploy approved patches; tamper with the delivery state | Deploy unapproved bytes undetected: every deployed digest is bound in the delivery proof link, and any verifier holding the public keys detects a chain mismatch. Its credentials are limited by the git host and registry. |
| Codeintel | Return misleading code excerpts | Execute code. Every excerpt carries a content hash checked against `git cat-file`, and the broker independently checks the archive hash. |
| Runner | Anything inside its own container | Reach the network, secrets, other tenants or the host (D2 §15.4 hardening plus Tier 2) |

## 5. Sandbox host decision rules (OD-04, C5, C8)

**Spike P0-11** evaluates WSL2 Ubuntu with Docker Engine (installed natively, not Docker Desktop) against each required control:

| Control | Requirement | Pass criterion |
|---|---|---|
| SB-1 | Native Docker Engine under systemd in a **dedicated WSL distro** | Engine runs; `docker info` shows cgroup v2 |
| SB-2 | Rootless mode | Runner processes are unprivileged on the host |
| SB-3 | Limits enforced (memory, pids, cpu, fsize) | Fork-bomb and memory-bomb probes contained |
| SB-4 | seccomp profile applied | Profile visible; blocked syscall denied |
| SB-5 | AppArmor profile applied | `aa-status` shows the profile enforcing *(the stock WSL kernel may not enable AppArmor)* |
| SB-6 | gVisor `runsc` | Escape probes pass under runsc |
| SB-7 | `--network=none` effective, including in WSL mirrored-networking mode | Network probes blocked |
| SB-8 | **Windows host exposure** | The distro has `[automount] enabled=false` and `[interop] enabled=false`. `/mnt/c` is absent. No Windows executables are reachable. |

**Decision**

- **WSL2 is acceptable for development and non-hostile tests only if SB-1 to SB-4, SB-7 and SB-8 pass.**
- All WSL distros share **one utility VM kernel**, so a kernel-level escape reaches the other distros and potentially the user's Windows files.
- Therefore **hostile workloads run only on a dedicated cloud Linux VM**, with Tier 2 required. Hostile workloads include the escape suite with live payloads, injection scenarios (S35 to S37), and any model-authored patch execution in campaigns.
- The exception is if SB-5 and SB-6 also pass **and** the owner accepts the documented shared-kernel residual risk in writing (an ADR).
- **Production deployments always use a dedicated Linux host with Tier 2.**

**C5.** The broker plus `build_sandbox_config()` is the policy point. No off-the-shelf socket proxy is relied on. The broker runs on the sandbox host with rootless Docker, and the host is treated as expendable: no application data, no database credentials beyond the broker's own role, rebuilt from an image.

## 6. Approvals (amends D2 §11)

- **Policy (R-07):** LOW/MEDIUM need ≥ 1 approver; HIGH needs ≥ 2 distinct approvers. A tenant may raise either minimum, but **never lower** it. Promotion needs separate approval(s) with the same counts.
- **Risk at decision (C2):** the API recomputes `domain.risk.assess()` from broker-written verification data plus the stored diff. If the result differs from `risk_assessments.level`, the decision is refused and audited (`risk_mismatch`). The recomputed level is stored in `approvals.risk_recomputed` and drives the approver count.
- **Passkey consent (ADR-015, V1):**
  - The challenge = SHA-256 of canonical JSON of `{tenant, patch_sha256, report_sha256, verification_report_sha256, scope, env, base_sha, jti, risk}`.
  - `userVerification=required`.
  - The assertion is stored on the approval and embedded in the token (`wa` claim).
  - The deployer verifies each assertion against `webauthn_credentials`: the credential is not revoked, `usable_from ≤ now`, the challenge recomputed from **its own** view of the hashes, and the sign count is monotonic where the authenticator supports it.
  - **Credential registration** requires an existing passkey or an admin recovery flow, is audited, and notifies the user and the tenant admins. A 24 h cool-down applies.
  - **Recovery:** two admins plus a re-identification step (procedure in runbook RB-07).
- **MVP:** the JWS token exactly as in D2 §11.2, plus the claims `ten` (tenant) and `vrs` (verification report sha256). The token format already reserves `wa`.

## 7. Keys

| Key | Holder | Algorithm | Rotation |
|---|---|---|---|
| Approval JWS | API | Ed25519, `kid` | 90 days; the old key verifies until its tokens expire |
| Execution attestation + verification proof link | Broker | Ed25519 | 90 days |
| Investigation proof link | Orchestrator | Ed25519 | 90 days |
| Delivery proof link | Deployer | Ed25519 | 90 days |
| Service-to-service tokens | One per service | Ed25519, 5-minute tokens | 30 days |
| Release HMAC | Per tenant and service (telemetry verifies) | HMAC-SHA256 | On demand |
| Git and registry credentials | Deployer, per tenant | Provider-issued | Per provider policy |
| Model provider keys | Orchestrator (platform or bring-your-own per tenant) | n/a | Per provider |

Public keys are published at `GET /v1/keys` (JWKS) and embedded in exported proof bundles with their validity windows. Rotation works without downtime (NFR-19, AT-OPS-07).

## 8. Prompt injection

All of D2 §16.2 applies unchanged. Additions:

- `human_note` items are also wrapped as untrusted.
- Prediction queries are schema-validated ASTs. They can only reference evidence IDs and bundle-scoped data, so an injected "query" can't reach anything outside the bundle.
- The injection scenarios (S35 to S37) run on the dedicated sandbox VM (§5).

## 9. Residual risks

These are carried in the risk register, 10 §6.

1. Kernel or runtime escape: reduced by Tier 2, eliminated only by Tier 3 microVMs (stretch).
2. A compromised broker forging results: limited by independent CI and human review.
3. A compromised API plus a compromised passkey at the same time.
4. Provider data handling outside our control: limited by the egress policy and redaction.
5. An adversarial patch subverting test reporting in ways not anticipated (D2 §15.8).
6. A WSL2 shared kernel, if the owner accepts it for development.
7. Side channels on a shared sandbox host across tenants. **Mitigation:** silo deployments or per-tenant sandbox hosts for sensitive tenants.
8. **The tenant context is not an authentication boundary** (confirmed while implementing migration 0001). Row-level security compares rows against `sentinel.tenant_id`, which any SQL client on a connection can set for itself.
   - **What RLS protects against:** application bugs, such as a missing `WHERE`, a wrong ID, or a context that was never set (fails closed, AT-TEN-02).
   - **What it doesn't protect against:** a compromised service process, which can switch to any tenant on its own connection.
   - **Isolation against a compromised process** requires per-tenant database credentials or silo deployment.
   - **Candidate V1 hardening:** set the context only through a SECURITY DEFINER function that verifies a short-lived, API-signed tenant token, with policies reading from a session table only that function can write. That needs an ADR and measurement before adoption.
