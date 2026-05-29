# Index sizing

| Tracks | Vectors (8 per track) | RAM (float32, 512-d) |
| --- | --- | --- |
| 1k    | 8k       | ~16 MB  |
| 10k   | 80k      | ~160 MB |
| 100k  | 800k     | ~1.6 GB |
| 1M    | 8M       | ~16 GB  |

Beyond 1M, switch to IVF+PQ or LanceDB.
