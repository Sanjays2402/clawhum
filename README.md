# ClawHum

Query-by-humming. Hum a melody or upload a clip, get ranked matches from a local library or Spotify catalog.

![landing](docs/screenshots/landing.png)

## What it does

Accepts an audio upload (hum, whistle, recorded clip), decodes it via `soundfile`/`librosa`, and runs it through a DSP pre-processing chain (butterworth biquad band-pass, pre-emphasis at 0.97, optional VAD trim). The cleaned signal is segmented into 6 s windows and embedded with CLAP (`laion/clap-htsat-unfused`) when ML extras are installed, or with a deterministic MFCC + chroma + spectral-contrast hash embedder as fallback. Embeddings are searched against a FAISS HNSW index (or a NumPy brute-force index on Apple Silicon where `faiss-cpu` is unavailable) and reranked by tempo proximity. Results stream back as scored track candidates with previews and artwork. A Prometheus `/metrics` endpoint and structured logs expose request volume, match counts, and index size.

## Features

- `POST /match` audio upload with configurable `top_k` and `threshold`, API-key gated.
- Local library + Spotify catalog ingestion via `services/indexer` and the `clawhum` CLI.
- FAISS HNSW index with NumPy fallback on `darwin/arm64`.
- LRU cache (`packages/match/clawhum_match/cache.py`, SHA1-keyed, capacity 256, 600 s TTL) for repeat queries.
- Request-ID middleware (echoes/generates `x-request-id`).
- In-process per-IP rate limit (120 req/min, bypasses `/health` and `/ready`).
- Prometheus text exposition at `/metrics`.
- Health probes at `/health` and `/ready`.
- Feedback endpoint (`POST /feedback`) for thumbs-up/down on results.
- Spotify OAuth flow (`/login`, `/callback`).
- Next.js 15 web UI with mic capture, results list, and recharts visualisations.
- 8 locale files under `web/i18n/` (de, en, es, fr, it, ja, ko, pt).
- OpenTelemetry FastAPI instrumentation, OTLP exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

## Stack

- Python 3.11, FastAPI, uvicorn, pydantic v2, typer CLI, structlog.
- Audio: numpy, scipy (butterworth via `scipy.signal.butter` + `sosfilt`), librosa, soundfile.
- ML (optional `[ml]` extra): torch, torchaudio, transformers, accelerate (CLAP).
- Index: faiss-cpu (non-darwin-arm64) with NumPy fallback.
- Web: Next.js 15, React 19 RC, Tailwind v4 beta, SWR, recharts, Phosphor Icons.
- Tracing: opentelemetry SDK + FastAPI instrumentation.

## Architecture

The API boots a single `AppState` holding the embedder, the index, and the track catalogue. Requests hit `/match`, the upload is decoded, pre-processed, segmented, embedded, and looked up in the index. The matcher applies a similarity threshold and an optional tempo-proximity rerank, then returns results. The indexer service scans `CLAWHUM_LIBRARY_PATH` and/or pulls Spotify metadata to (re)build the index on disk.

```
                +------------------+
  mic upload -->|  FastAPI /match  |
                +--------+---------+
                         |
                         v
   +-----------------------------------------------+
   | DSP: load_audio -> biquad band-pass ->        |
   |      pre_emphasis(0.97) -> segment_query      |
   +-----------------------------------------------+
                         |
                         v
            +-------------------------+
            | Embedder                |
            |   CLAP (HF) | HashEmb   |
            +-----------+-------------+
                        |
                        v
            +-------------------------+        +-------------------+
            | FAISS HNSW | NumPy idx  |<------>|  catalog tracks   |
            +-----------+-------------+        +-------------------+
                        |
                        v
            +-------------------------+
            | Matcher: top-k + tempo  |
            +-----------+-------------+
                        |
                        v
            +-------------------------+        +-------------------+
            | JSON results            |------->| Next.js web UI    |
            +-------------------------+        +-------------------+
```

## Quick start

```bash
# install (dev + tests, no torch)
make install
# or with CLAP:
uv pip install -e ".[dev,ml]"

# seed fixtures + index without CLAP + serve API
make dogfood

# API only
make serve              # uvicorn on :7451

# Web
make web                # next dev on :7452
```

Dev ports: API `7451`, web `7452`.

One-liner reproduce path: `make dogfood` runs `install`, `seed`, `clawhum index ./data/audio --no-clap`, `clawhum stats`, then `serve`.

## Configuration

From `.env.example`:

| Variable | Default | Purpose |
|---|---|---|
| `CLAWHUM_API_KEY` | `changeme` | Required header `x-api-key` for `/match` |
| `CLAWHUM_LOG_LEVEL` | `INFO` | structlog level |
| `CLAWHUM_INDEX_PATH` | `./data/index/clawhum.faiss` | Index file on disk |
| `CLAWHUM_LIBRARY_PATH` | `./data/audio` | Audio source for indexing |
| `CLAWHUM_MODEL_ID` | `laion/clap-htsat-unfused` | HF model id for CLAP |
| `CLAWHUM_DEVICE` | `auto` | `cpu`, `cuda`, `mps`, or `auto` |
| `CLAWHUM_TOP_K` | `10` | Default match result count |
| `CLAWHUM_THRESHOLD` | `0.20` | Min cosine similarity to keep |
| `SPOTIFY_CLIENT_ID` | | OAuth client id |
| `SPOTIFY_CLIENT_SECRET` | | OAuth secret |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:7451/auth/spotify/callback` | OAuth redirect |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | | If set, exports traces via OTLP |

## Scripts

`scripts/`:

- `bootstrap.sh` create `.venv` and install editable.
- `dev_api.sh` activate venv and run uvicorn with reload.
- `dev_web.sh` `cd web && npm install && npm run dev`.
- `dogfood.sh` end-to-end: venv, seed, index without CLAP, serve.
- `format.sh` `ruff check --fix && ruff format`.
- `release.sh vX.Y.Z` tag and push.
- `seed_fixtures.py` generate synthetic audio fixtures into `./data/audio`.

Makefile targets: `install`, `test`, `lint`, `fmt`, `seed`, `serve`, `web`, `dogfood`, `docker`.

## API

Match:

- `POST /match` multipart `audio` (file), optional form `top_k`, `threshold`. Requires `x-api-key`. Returns `MatchResponse` with `query_id`, `elapsed_ms`, `count`, `results[]`.

Library:

- `GET /stats` index size, track count, model id.
- `POST /reindex` rebuild index from `CLAWHUM_LIBRARY_PATH`.

Feedback:

- `POST /feedback` record up/down on a `query_id` + `track_id`.

Spotify:

- `GET /login` start OAuth.
- `GET /callback` OAuth redirect target.

Health & metrics:

- `GET /health` liveness.
- `GET /ready` readiness (checks index + embedder).
- `GET /metrics` Prometheus text exposition.

## Fingerprinting

The fallback `HashEmbedder` (`packages/embed/clawhum_embed/fallback.py`) extracts:

- 40 MFCC coefficients (`librosa.feature.mfcc`, time-averaged).
- 12 chroma bins via constant-Q (`chroma_cqt`, time-averaged).
- Spectral contrast bands, time-averaged.

These are concatenated and projected by a seeded Gaussian matrix to a fixed 512-d unit vector. CLAP (`laion/clap-htsat-unfused`) produces 512-d embeddings directly.

Pre-processing chain (`packages/audio/clawhum_audio/`):

- Butterworth biquad filters (`filters/biquad.py`): `high_pass` default 80 Hz, `low_pass` default 8 kHz, `band_pass` 80 to 8000 Hz, order 4, SOS form.
- `pre_emphasis(x, coef=0.97)` first-order high-pass.
- Segmentation: training/index uses 6.0 s windows with 3.0 s hop (`segment_fixed`); queries are cropped to 10 s max (`segment_query`).
- Resample target: 48 kHz.

## Metrics

Exposed at `GET /metrics` (text/plain, Prometheus format):

- `clawhum_uptime_seconds` (counter) process uptime.
- `clawhum_index_vectors` (gauge) vectors in index.
- `clawhum_index_tracks` (gauge) tracks in catalog.
- `clawhum_requests_total` (counter) HTTP requests served.
- `clawhum_match_total` (counter) `/match` calls.
- `clawhum_match_latency_sum_s` (counter) cumulative `/match` latency.

## Project structure

```
clawhum/
├── cli/                  # clawhum CLI (typer)
├── packages/
│   ├── audio/            # DSP: io, preprocess, filters, segment, features, vad
│   ├── core/             # settings, logging, telemetry, version
│   ├── embed/            # CLAP + HashEmbedder fallback
│   ├── index/            # FAISS HNSW + NumPy fallback
│   ├── library/          # local + Spotify track sources
│   └── match/            # matcher, LRU cache, scoring
├── services/
│   ├── api/              # FastAPI app, routes, metrics, middleware
│   └── indexer/          # batch indexer
├── web/                  # Next.js 15 frontend, i18n (8 locales)
├── scripts/              # bootstrap, dogfood, dev_api, dev_web, format, release
├── configs/              # runtime configs
├── data/                 # audio fixtures and index files
├── infra/                # docker, helm, otel
├── tests/                # pytest suite
├── Makefile
├── pyproject.toml
└── .env.example
```

## License

MIT. See `LICENSE`.
