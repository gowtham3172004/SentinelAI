# 05. API amendments

Amends D2 §14. These conventions stay unchanged: `/v1`, RFC 9457 problem details, cursor pagination, `Idempotency-Key`, `/healthz` and `/readyz`, and SSE with event IDs.

## 1. Cross-cutting changes

- **Tenant scoping.** Human requests carry a tenant through the path prefix `/v1/t/{tenant}/…`. The API checks the caller's membership and sets `sentinel.tenant_id` for the transaction. Platform endpoints (`/v1/platform/…`) need `platform_admin`.
- **Service tokens** carry `svc` and `ten` claims. The receiving service rejects any token whose service isn't on that endpoint's allow-list (AT-SEC-03).
- **OpenAPI** is snapshot-tested (D2 §19) and generates the dashboard's TypeScript client.
- **Long-running work** returns `202` with a resource URL. Clients follow it through SSE.

## 2. Public API (`sentinel-api`): changes and additions

| Method and path | Purpose | Minimum role | Change |
|---|---|---|---|
| `POST /v1/events/releases` | **Moved to sentinel-telemetry** (B1). Requires HMAC, a timestamp and an idempotency key. | service | Moved |
| `POST /v1/t/{tenant}/runs/{id}/notes` | Add a human note to a run (FR-33) | responder | New |
| `GET /v1/t/{tenant}/proofs/{patch_id}` | Proof bundle (JSON, DSSE envelopes plus public keys) | viewer | New |
| `GET /v1/keys` | JWKS of all public signing keys with validity windows | public | New |
| `POST /v1/t/{tenant}/patches/{id}/decisions` | Unchanged body plus `diff_sha256`, `report_sha256` and, in V1, `webauthn_assertion`. Returns `risk_recomputed`. | approver | Extended |
| `POST /v1/t/{tenant}/patches/{id}/decisions/challenge` | Get the passkey challenge for a decision (V1) | approver | New |
| `POST /v1/webauthn/register/{begin,finish}` | Register a passkey (existing-passkey or admin recovery path) | authenticated | New (V1) |
| `GET/POST/DELETE /v1/t/{tenant}/notification-channels` | Configure webhooks (FR-30) | admin | New |
| `GET/PATCH /v1/t/{tenant}/settings/data-policy` | Egress, retention, provider policy (FR-22) | admin | New |
| `POST /v1/platform/tenants`, `PATCH /v1/platform/tenants/{id}` | Create, suspend or offboard a tenant (offboarding triggers erasure) | platform_admin | New |
| `GET /v1/platform/providers` | Provider registry, capability probe results, quota state | platform_admin | New |
| Every endpoint in D2 §14.2 | Unchanged semantics, now under `/v1/t/{tenant}` | as in D2 | Re-scoped |

## 3. Internal APIs

| Service | Endpoint | Caller | Change |
|---|---|---|---|
| sentinel-telemetry | `POST /v1/otlp/{logs,metrics,traces}` | Collector | Unchanged; tenant from the collector token |
| sentinel-telemetry | `POST /v1/events/releases` | CI and deploy scripts | Moved here |
| sentinel-telemetry | `POST /v1/alerts/alertmanager` | Alertmanager (V1) | New |
| sentinel-codeintel | `POST /v1/tools/{tool}` | Orchestrator | Unchanged |
| sentinel-codeintel | `GET /v1/revisions/{sha}/archive` | Broker | New (D2): streams a `git archive` tar, `X-Content-SHA256` header |
| sentinel-broker | `POST /v1/executions` | Orchestrator | **Returns 202** `{request_id}`; the result arrives as an `execution.completed` event and through `GET /v1/executions/{request_id}` |
| sentinel-broker | `POST /v1/verifications` | Orchestrator | **Returns 202**; result through `verification.completed` |
| sentinel-broker | `GET /v1/attestation-keys` | Any | Public keys |

The broker still accepts only template IDs plus validated parameters, and never a command string (D2 §14.3).

## 4. Events added to the catalogue (D2 §4.3)

| Event | Producer | Consumers |
|---|---|---|
| `run.parked` / `run.resumed` | orchestrator | UI |
| `run.note_added` | API | orchestrator, UI |
| `proof.link_added` | orchestrator, broker, API, deployer | UI |
| `provider.quota_low` / `provider.unavailable` | orchestrator | UI, notifier, ops alerts |
| `tenant.offboarding_started` / `tenant.erased` | API, maintenance | audit |
| `credential.registered` / `credential.revoked` | API | notifier, audit |
