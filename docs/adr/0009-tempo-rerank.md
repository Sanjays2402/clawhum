# ADR 0009: Tempo proximity rerank

Cosine alone confuses tracks of similar timbre. Tempo proximity
(including half/double matches) re-orders the top candidates.
Weight: 0.15, additive. Configurable in `tempo_rerank`.
