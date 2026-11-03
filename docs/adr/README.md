# Architecture Decision Records

Format: `docs/adr/NNNN-title.md` (template: `0000-template.md`). Status values: Proposed · Accepted · Superseded by ADR-NNNN.

| ADR | Decision | Status | Source |
|---|---|---|---|
| 001 | Explicit state machine, not a framework-driven agent | Accepted | D2 §8 (file written in Phase 0, B04) |
| 002 | Modular monolith; security-critical parts run as separate processes | Accepted | D2 §3 |
| 003 | PostgreSQL job queue + transactional outbox | Accepted | D2 §4 |
| 004 | Deterministic detection before learned detection | Accepted | D2 §6 |
| 005 | Exact and syntax-aware search first; embeddings for the KB and as a fallback | Accepted | D2 §7 |
| 006 | Signed approvals verified by the deployer | Accepted; extended by 015 | D2 §11 |
| 007 | Tiered sandbox | Accepted; amended by design/04 §5 (Tier 2 in the MVP) | D2 §15 |
| 008 | The model supplies files, never commands | Accepted | D2 §9 |
| 009 | Python-only targets (MVP, V1) | Accepted | D2 §1 |
| 010 | Telemetry in PostgreSQL for the prototype | Accepted; complemented by the `TelemetrySource` port | D2 §5 |
| 011 | Hidden tests and held-out test split | Accepted | D2 §18 |
| 012 | Evidence ladder instead of numeric confidence | Accepted; amended by 013 | D2 §8 |
| [013](0013-prediction-engine.md) | Prediction engine (executable falsification) | Proposed | Architecture review |
| [014](0014-proof-bundles.md) | Cryptographic proof bundles (in-toto/DSSE, signed per authority) | Proposed | Architecture review |
| [015](0015-passkey-consent.md) | Passkey (WebAuthn) human consent verified by the deployer | Proposed | Architecture review |
| [016](0016-open-benchmark.md) | Open end-to-end benchmark | Proposed | Architecture review |
| [017](0017-llm-gateway.md) | Provider-independent LLM gateway; Groq free tier first | Proposed | OD-01, OD-03 |
| [018](0018-local-embeddings.md) | Local embeddings behind an embedder port | Proposed | OD-02 |
| [019](0019-multi-tenancy.md) | Multi-tenancy: pooled with RLS by default, silo optional | Proposed | OD-12 |
| [020](0020-async-broker.md) | Async broker execution with broker-side signature recomputation | Proposed | Findings C1, D1 |
| [021](0021-delivery-modes.md) | Delivery modes: PR-only and managed | Proposed | Finding E2 |
| [022](0022-listen-notify.md) | PostgreSQL LISTEN/NOTIFY replaces Redis in the control plane | Proposed | Finding E3 |

ADRs 001–012 get their own files in Phase 0 (step B04), summarising the D2 sections above.
