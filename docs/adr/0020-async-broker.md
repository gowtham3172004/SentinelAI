# ADR-0020: Async broker execution with broker-side signature recomputation

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-09, FR-11, NFR-02, INV-4

## Context
- Suite runs take up to 600 s, which is too long for synchronous HTTP.
- D2 writes executions only on completion, so in-flight work is invisible.
- Gate G01 trusted an orchestrator-written reproduction row.

## Decision
- `POST /v1/executions` returns 202.
- Requests live in the broker-owned `execution_requests` table and are processed from a broker queue.
- Completion is announced by `execution.completed`.
- Sandbox executions stay insert-only on completion. At most one retry.
- G01 and G03 use **only** the broker's own recomputation of the failure signature, from attested executions and the trusted `sentinel-pytest` report, cross-checked against JUnit and the collection count.

## Consequences
- **Positive:**
  - No long HTTP calls.
  - In-flight work is visible and recoverable.
  - The orchestrator can't forge the reproduction gate.
- **Negative:** one more queue to operate. It reuses the jobs-table design.

## Alternatives considered
- Synchronous calls with long timeouts: rejected as fragile.
- Trusting the orchestrator's reproduction outcome: rejected (finding C1).

## Acceptance
AT-BRK-03, AT-BRK-06, AT-BRK-07.
