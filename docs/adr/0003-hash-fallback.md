# ADR 0003: Deterministic hash embedder fallback

## Context
CI and air-gapped users cannot fetch CLAP weights.

## Decision
Project MFCC + chroma + spectral-contrast features through a fixed
random matrix seeded from a constant. Same dim as CLAP for index parity.

## Consequences
Lower precision; same code paths exercised.
