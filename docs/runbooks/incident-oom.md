# Runbook: OOM during indexing

- Drop `max_segments_per_track` from 8 to 4.
- Batch size in `embed_batch` is implicit (whole-track segments);
  process tracks one at a time (already the default).
