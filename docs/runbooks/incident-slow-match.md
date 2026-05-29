# Runbook: slow /match (>3 s)

1. Check `CLAWHUM_DEVICE` and embedder class via `/health`.
2. Switch to GPU/MPS with `CLAWHUM_DEVICE=cuda`.
3. Reduce `top_k * candidate_mult` in `Matcher.match`.
4. Profile with `py-spy top --pid $(pgrep -f clawhum_api)`.
