# ADR 0001: Use CLAP for audio embeddings

## Context
Need a model that maps hums and full tracks to the same space.

## Decision
Use `laion/clap-htsat-unfused`. 512-d output, joint audio/text training,
proven on music retrieval benchmarks.

## Consequences
- 1.5 GB weights, not bundled.
- Inference is heavy on CPU; CUDA/MPS strongly preferred.
