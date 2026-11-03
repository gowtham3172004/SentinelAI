# ADR-0018: Local embeddings behind an embedder port

- Status: Proposed
- Date: 2026-10-31
- Requirements affected: FR-16, FR-22, FR-24

## Context
Paid embedding APIs aren't available, and sending incident text to an external embedder is an avoidable privacy exposure.

## Decision
- An `Embedder` port.
- The default adapter is fastembed (ONNX on CPU) with `BAAI/bge-small-en-v1.5` (384 dimensions).
- Vectors are stored per model in `kb_case_embeddings`, with one HNSW expression index per model.
- External providers become adapters behind the same port.
- Changing the model schedules re-embedding.

## Consequences
- **Positive:**
  - Free.
  - Private (no egress).
  - Deterministic.
- **Negative:**
  - Lower retrieval quality than large embedding models. Measured by precision@3, and swappable.

## Alternatives considered
- An external embedding API: deferred; available later as an adapter.
- sentence-transformers with PyTorch: rejected as a much heavier runtime for the same models.

## Acceptance
AT-EMB-01, AT-KB-01.
