# ADR-0019: Multi-tenancy (pooled with RLS by default, silo optional)

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-21, NFR-17, NFR-20. Supersedes D2 §13.10 "Row-level security is not required".

## Context
Sentinel must be deployable for several teams or customers. Retrofitting tenancy later is the classic source of cross-tenant leaks.

## Decision
- `tenant_id` on every tenant-scoped table.
- `FORCE ROW LEVEL SECURITY` through `current_tenant()`, which raises if no context is set.
- Queue and outbox are system-scoped, with payloads of IDs only.
- Per-tenant audit chains, secrets, mirrors, quotas and sandbox labels.
- No cross-tenant knowledge base.
- A single `default` tenant initially, with full enforcement switched on.
- Silo deployment (one stack per tenant) for strict customers.
- Batched `INSERT … unnest` replaces `COPY` on RLS tables (R-10).
- Specification: `design/01-architecture.md` §4 and `design/02-data-model.md` §2–3.

## Consequences
- **Positive:**
  - Isolation is exercised from day one.
  - Per-chain audit locking reduces contention.
- **Negative:**
  - Every transaction must carry tenant context.
  - RLS overhead on hot paths must be measured (RK-05).

## Alternatives considered
- Schema per tenant: rejected for migration fan-out.
- Retrofitting later: rejected as high risk.

## Acceptance
AT-TEN-01 to AT-TEN-05, AT-AUD-03.
