# ADR-0022: PostgreSQL LISTEN/NOTIFY replaces Redis in the control plane

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: F9, NFR-10

## Context
In D2, Sentinel's control plane uses Redis only for live UI fan-out and rate limits. Both can be served by PostgreSQL, which is already the system of record.

## Decision
- The outbox dispatcher issues `NOTIFY`, with the event ID as the only payload.
- The API's SSE hub `LISTEN`s and reads the event row.
- Rate limits use in-process token buckets seeded from the database.
- Redis is removed from Sentinel's control plane.
- The reference application keeps its own queue (Valkey).

## Consequences
- **Positive:**
  - One less control-plane dependency.
  - Events are transactionally consistent with state.
- **Negative:**
  - NOTIFY isn't durable, so a reconnecting SSE hub replays from the outbox by last event ID.
  - Large multi-node fan-out would need revisiting.

## Alternatives considered
- Keep Redis pub/sub: rejected as an unnecessary moving part at this scale.

## Acceptance
AT-Q-04, AT-UI-02.
