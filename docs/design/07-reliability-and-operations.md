# 07. Sentinel's own reliability, observability and deployment

Amends D2 §17 and adds the reliability requirements decided under OD-13. NFR-02 (crash-safe resume), NFR-05 (budgets) and NFR-10 (operability) from D2 still apply.

## 1. Reliability requirements

| ID | Requirement | Target (initial) | Phase | Test |
|---|---|---|---|---|
| NFR-11 | Backup and restore | **MVP:** RPO ≤ 24 h (nightly `pg_dump` plus blob sync), RTO ≤ 2 h. **V1:** RPO ≤ 15 min (WAL archiving with point-in-time recovery), RTO ≤ 1 h. A restore drill runs in the nightly CI. | 2 / 7 | AT-OPS-01 |
| NFR-12 | Degraded modes | Provider outage or quota: detection, incidents, UI and notifications keep working; runs park (03 §2). Broker down: runs retry, then escalate. Database down: services report not-ready and recover automatically. | 2–4 | AT-OPS-04/05 |
| NFR-13 | Graceful shutdown and startup reconciliation | SIGTERM: stop claiming, finish or checkpoint the current step within 30 s, release leases. On start: reap expired leases, remove orphaned containers, move deliveries stuck in non-terminal states to `failed_safe` and attempt rollback, re-dispatch the pending outbox. | 0–6 | AT-OPS-02/03 |
| NFR-14 | Safe upgrades | Expand-and-contract migrations only (no destructive change in the same release as the code that depends on it). Workflow version pinned per run (03 §2). API versioned. Mixed old and new workers are safe during a rolling restart. | 0+ | AT-WF-07, AT-DB-01 |
| NFR-15 | Monitoring that doesn't depend on Sentinel | A heartbeat to an external dead-man's switch (configurable URL). The alert fires if it's missing for 5 minutes. An optional local watchdog container for air-gapped installs. | 2 / 7 | AT-OPS-06 |
| NFR-16 | Burst ingest | 1,000 events/s sustained; at a 2,000/s burst, 100% of error and exception records kept, shedding only debug and info (counted and reported as a data-quality signal) | 2 | AT-ING-01/02 |
| NFR-17 | Tenant isolation | Zero cross-tenant reads or writes in generated and property-based tests | 0+ | AT-TEN-01 to 03 |
| NFR-18 | Licence compliance | Every runtime dependency Apache-2.0-compatible; CI licence check against an allow-list | 0 | AT-LIC-01 |
| NFR-19 | Key rotation without downtime | New `kid` active; old keys verify until the expiry of what they signed | 5 / 7 | AT-OPS-07 |
| NFR-20 | Quota fairness | Per-tenant quotas on runs, tokens, requests, executions and storage; one tenant can't starve another | 3 / 4 | AT-TEN-05 |
| REL-1 | Bounded resources for Sentinel processes | Memory limits per container; bounded in-memory queues; back-pressure (HTTP 429/503 with `Retry-After`) rather than OOM | 2+ | AT-ING-02 |
| REL-2 | Poison work | A job failing `max_attempts` times → `dead`, alert, never blocks its queue | 0 | AT-Q-02 |
| REL-3 | Clock discipline | Hosts run NTP; skew over 5 s is flagged on telemetry (D2 §5.2); the audit ts comes from the database clock | 0 | AT-ING-04 |
| REL-4 | Maintenance failure visibility | A failed partition creation, retention job or backup alerts within one cycle | 2 | AT-OPS-01 |

## 2. Failure-mode catalogue

Each row has a chaos or integration test. This extends D2 §4.5.

| Component | Failure | Detected by | Behaviour | Recovery |
|---|---|---|---|---|
| Any worker | Crash mid-step | Lease expiry | Another worker re-runs the current state; idempotent effects | Automatic |
| Orchestrator | Provider 429 or daily quota | Gateway | Park, emit `provider.quota_low` | Resumes when the bucket refills |
| Orchestrator | Provider model retired | Capability probe, or a 404 | Model marked unavailable; failover within its equivalence class (recorded) | Operator updates the registry |
| Broker | Crash during execution | Request in `running` past its deadline | Container killed by the reaper; request `killed`; retried at most once | Automatic |
| Broker | Sandbox host unreachable | Health check | Requests stay queued; runs park, then `ESCALATED_DEPENDENCY_UNAVAILABLE` | Operator |
| PostgreSQL | Outage | `/readyz` failures | Services back off; the collector buffers (bounded); a gap is recorded | Automatic on return |
| PostgreSQL | Disk ≥ 85% | Metric | Alert; retention tightened to the policy minimum | Operator |
| Outbox dispatcher | Crash after publish, before marking | Redelivery | Consumers deduplicate by `event_id` | Automatic |
| Deployer | Crash mid-delivery | Startup reconciliation | `failed_safe` + rollback attempt + notification | Human |
| Telemetry | Burst above capacity | Ingest lag metric | Shed debug and info; keep errors | Automatic |
| Notifier | Endpoint down | Attempt status | Exponential retry for 24 h, then the channel is flagged | Admin |
| Audit | Chain break | Daily verify | Alert, freeze approvals for that tenant until reviewed | Security runbook |
| Keys | Key compromise | Report | Revoke `kid`; tokens under it refused; re-sign open proof links | RB-06 |

## 3. Backup and disaster recovery

- **MVP:**
  - Nightly logical backup (`pg_dump -Fc`) and a blob-store sync to a separate location.
  - Backups encrypted.
  - The audit chain heads are exported with every backup and daily to an external location (D2 §16.5).
  - **The nightly CI restores the latest backup into a fresh database and runs `audit verify` plus a smoke test.**
- **V1:**
  - WAL archiving for point-in-time recovery, with a documented RPO measurement.
  - Escrow of signing keys (sealed, two-person retrieval).

## 4. Observability of Sentinel

D2 §17 stays as written. Additions:

- **Metrics:**
  - Provider quota remaining and parked runs.
  - Tenant label on business metrics only (bounded cardinality; telemetry hot paths aggregated per tenant).
  - RLS denials.
  - Backup age.
  - Dead-man heartbeat status.
  - Proof-link verification failures.
- **Destination:** Sentinel exports its own OTLP to an operator-chosen backend. The dev compose file has an optional `observability` profile. Sentinel's own telemetry is **never** routed into its own detector (D2 §17.1).
- **Alerts** (in addition to D2 §17.4): backup older than 26 h; restore drill failed; provider unavailable for more than 30 min; cross-tenant access denied (indicates a bug or an attack); dead-man missed.

## 5. Deployment

| Topology | Use | Contents |
|---|---|---|
| Dev (Compose on the WSL2 distro) | Development | Compose profiles: `core` (postgres + pgvector, collector), `sentinel` (services), `target` (reference app on Valkey), `observability` (optional), `idp` (Keycloak). The sandbox host is the WSL distro (non-hostile work) or the cloud VM. |
| Single-node product | First real deployments | Compose bundle with pinned digests, a `.env` from secrets, TLS through a reverse proxy, a **separate** sandbox host VM, backups configured |
| Silo | Sensitive tenants | The same bundle per tenant |
| Later | Scale | Helm or Kubernetes (Kubernetes stays a non-goal for MVP and V1, D1 N8) |

- **Images:** built in CI, pinned by digest, SBOM generated (syft), vulnerability-scanned. Signing release images (cosign) is optional in V1.
- **Configuration:** a typed settings schema validated at startup. Fail fast on invalid or missing settings. No secrets in environment dumps or logs.
- **Health:** every process exposes `/healthz` (liveness) and `/readyz` (dependencies) and drains on SIGTERM.

## 6. Runbooks (written in Phase 7; stubs in Phase 0)

| ID | Runbook |
|---|---|
| RB-01 | Restore from backup |
| RB-02 | Provider outage or quota exhaustion |
| RB-03 | Sandbox host rebuild |
| RB-04 | Audit chain break |
| RB-05 | Tenant offboarding and erasure |
| RB-06 | Key rotation and compromise |
| RB-07 | Passkey recovery |
| RB-08 | Stuck delivery and manual rollback |
| RB-09 | Upgrade procedure |
