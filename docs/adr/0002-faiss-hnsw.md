# ADR 0002: FAISS HNSW

## Context
We need fast top-k cosine search over O(10^5..10^6) vectors with
incremental add.

## Decision
HNSW graph (M=32, efConstruction=200, efSearch=64) on inner product
over L2-normalized vectors.

## Consequences
- Memory cost ~ 4 * dim * N + graph overhead.
- For >10M vectors, switch to IVF+PQ or LanceDB on disk.
