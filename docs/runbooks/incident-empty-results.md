# Runbook: empty match results

1. `GET /stats`: confirm `vectors > 0`.
2. If 0, run `clawhum index <dir>` and check the indexer logs.
3. If >0, lower `CLAWHUM_THRESHOLD` and retry.
4. If still empty, confirm input audio decodes (`ffprobe`).
