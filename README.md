# ClawHum

Query-by-humming. Hum a melody, get matching tracks from your library or Spotify.

```
    you hum 5 sec    -->  CLAP embedding  -->  FAISS HNSW  -->  top-k tracks
                             (or HashEmbedder fallback)            +tempo rerank
```

## Architecture

```
+------------------+        +-------------------+        +-------------------+
|   web (Next.js)  | ---->  |   API (FastAPI)   | ---->  |   Vector index    |
|  record / list   |  http  |  /match /reindex  |  load  |   FAISS or NumPy  |
+------------------+        +---------+---------+        +---------+---------+
                                      |                            ^
                                      v                            |
                            +---------+---------+        +---------+---------+
                            |   CLAP embedder   |  feed  |     Indexer       |
                            |  laion/clap-htsat |  -->   |  local + Spotify  |
                            +-------------------+        +-------------------+
```

## Quickstart

```bash
# 1. install
uv venv && uv pip install -e ".[dev,ml]"

# 2. index a music folder
clawhum index ~/Music

# 3. serve
clawhum serve     # http://localhost:7451

# 4. open the web UI
cd web && npm install && npm run dev   # http://localhost:7452
```

### Dogfood (one-liner)

```bash
pytest -q && clawhum index ./tests/fixtures && clawhum serve
```

## CLAP model

The CLAP weights are **not bundled**. First run pulls them from HuggingFace
(`laion/clap-htsat-unfused`, ~1.5 GB) into `~/.cache/huggingface`. To pre-fetch:

```bash
python -c "from transformers import ClapModel, ClapProcessor; \
  ClapModel.from_pretrained('laion/clap-htsat-unfused'); \
  ClapProcessor.from_pretrained('laion/clap-htsat-unfused')"
```

If you skip the ML extras, ClawHum falls back to a deterministic
spectral-hash embedder (MFCC + chroma + spectral contrast projected to 512-d).
Useful for CI and air-gapped dev. Quality is lower; CLAP is recommended.

## API

| Method | Path                    | Notes                              |
| ------ | ----------------------- | ---------------------------------- |
| POST   | `/match`                | multipart `audio`, returns ranked  |
| GET    | `/stats`                | tracks / vectors / backend         |
| POST   | `/reindex`              | rebuild from local dir or Spotify  |
| POST   | `/feedback`             | thumbs +/- per match               |
| GET    | `/health` `/ready`      | health probes                      |
| GET    | `/auth/spotify/login`   | OAuth2 auth code flow              |

All write paths require `X-API-Key` header.

## Config

`pydantic-settings` reads `CLAWHUM_*` env vars. See `.env.example`.

| Var | Default | Meaning |
| --- | --- | --- |
| `CLAWHUM_INDEX_PATH` | `./data/index/clawhum.faiss` | index artifact |
| `CLAWHUM_LIBRARY_PATH` | `./data/audio` | local scan root |
| `CLAWHUM_MODEL_ID` | `laion/clap-htsat-unfused` | CLAP model |
| `CLAWHUM_DEVICE` | `auto` | `cpu` `cuda` `mps` |
| `CLAWHUM_TOP_K` | `10` | default result count |
| `CLAWHUM_THRESHOLD` | `0.20` | min cosine similarity |

## Docker

```bash
docker compose -f infra/docker/docker-compose.dev.yml up --build
```

## Helm

```bash
helm install clawhum infra/helm/clawhum --set apiKey=$(openssl rand -hex 16)
```

## Tradeoffs

- **CLAP not bundled.** 1.5 GB weights; users opt in.
- **FAISS HNSW vs LanceDB.** Picked FAISS HNSW for in-memory recall on
  modest libraries. LanceDB is a drop-in we may add for on-disk billion-scale.
- **Hash fallback.** Deterministic but weaker. Good enough for CI.
- **Spotify preview URLs.** Many tracks no longer expose previews. Local
  audio gives the highest match quality.
- **Tempo rerank weight = 0.15.** Tuned by hand; expose via settings later.
- **Auth.** Single static API key. Add JWT / OAuth in v0.2.

## License

MIT.
