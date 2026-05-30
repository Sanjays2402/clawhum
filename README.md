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

## Operations

Day-2 operational concerns for running ClawHum in a shared or production environment.

### API keys, roles, and per-key rate limiting

ClawHum supports multiple named API keys with independent rate-limit
buckets and per-key roles. Configure via `CLAWHUM_API_KEYS` with a
comma-separated spec:

```
CLAWHUM_API_KEYS=ops:sk_live_ops:600:admin,partner:sk_live_partner:120:writer|reader,readonly:sk_ro_xyz:60:reader
CLAWHUM_RATE_LIMIT_PER_MINUTE=120
```

Each entry is `name:secret[:rpm[:role1|role2|...[:tenant_id]]]`. The optional `rpm`
overrides the default from `CLAWHUM_RATE_LIMIT_PER_MINUTE` for that key
only, so noisy partners cannot starve interactive traffic. Buckets are
per-key sliding windows; unauthenticated requests fall back to a per-IP
bucket sized by the default. When no keys are configured the API runs
in open dev mode and grants the full role set.

Known roles and the routes they unlock:

- `reader` may call `/match`, `/stats`, and `/v1/privacy/export`.
- `writer` adds `/reindex` and `/feedback`.
- `admin` adds destructive routes such as `DELETE /v1/privacy/me` and
  implicitly satisfies every other role.

Unknown role tokens are dropped at parse time so a typo cannot widen
access. A key with no roles can authenticate but will receive `403` on
any role-gated route. Audit log entries include the resolved
`api_key_name` and `roles` so reviews can answer who did what with what
scope.

The legacy `CLAWHUM_API_KEY` setting is still honoured and registered
as the `default` key (full role set) when `CLAWHUM_API_KEYS` is empty,
so existing deployments keep working without changes.

Clients receive `X-RateLimit-Limit` and `X-RateLimit-Remaining` on
every response, plus `Retry-After` on `429`s, so they can pace requests
without guesswork. Audit log `actor` ids are hashed digests of the
supplied key, so rotating a leaked secret is a one-line config change.

For multi-replica deployments the in-process limiter should be replaced
with a shared store (Redis); the bucket id format (`key:<name>` or
`ip:<addr>`) is stable so the swap is mechanical.

Clients receive `X-RateLimit-Limit` and `X-RateLimit-Remaining` on every
response, plus `Retry-After` on `429`s, so they can pace requests
without guesswork. Audit log `actor` ids are hashed digests of the
supplied key, so rotating a leaked secret is a one-line config change.

For multi-replica deployments the in-process limiter should be replaced
with a shared store (Redis); the bucket id format (`key:<name>` or
`ip:<addr>`) is stable so the swap is mechanical.

### Multi-tenant scoping

Every API key is bound to a `tenant_id`. The tenant is the optional
fifth field in `CLAWHUM_API_KEYS`, defaulting to the key name when
omitted so single tenant deployments keep working unchanged. Tenant
ids are normalised to lowercase `[a-z0-9-_]` at parse time so a noisy
spec entry cannot smuggle path traversal or log injection into
downstream sinks.

```
CLAWHUM_API_KEYS=ops:sk_live_ops:600:admin:acme,partner:sk_live_partner:120:writer|reader:globex
```

The resolved tenant rides through the request lifecycle in three
places:

- `request.state.tenant_id` is set by the auth dependency and exposed
  through the `current_tenant` FastAPI dependency for route handlers
  that need it.
- Every response carries an `X-Tenant-Id` header and every structlog
  line emitted during the request is bound with `tenant_id=...` for
  cross-service correlation.
- Audit events include the tenant id alongside the actor digest, so a
  shared bus or SIEM can group activity per tenant without rejoining
  against the key registry.

Feedback rows persisted under `CLAWHUM_FEEDBACK_PATH` carry the
writer's `tenant_id`. The privacy export endpoint returns only the
caller's tenant's feedback rows, and the privacy delete endpoint
redacts the identifying fields on those rows in place while preserving
the row shape so aggregate analytics stay valid. Rows written before
multi tenancy was enabled have no `tenant_id` tag and are surfaced
only to the `default` tenant rather than orphaned.

Dev mode (no `CLAWHUM_API_KEYS` set) collapses to a single `dev`
tenant so local workflows stay frictionless.

### Request correlation and trace context

Every request is tagged with a request id and a W3C Trace Context span
so log lines, audit rows, and downstream calls can be stitched back
together without guesswork.

The `RequestIDMiddleware` runs as the outermost middleware and does
four things on each request:

- Accepts an inbound `X-Request-ID` header or generates a fresh UUID4.
- Accepts an inbound `traceparent` header (W3C Trace Context, version
  `00`) and reuses its 128 bit trace id. If the header is missing,
  malformed, or carries the reserved `ff` version, a fresh trace id is
  generated from `secrets.token_hex(16)`.
- Mints a new 64 bit span id for this hop so the service appears as a
  distinct span in any downstream collector even when forwarding.
- Binds `request_id`, `trace_id`, `span_id`, `method`, and `path` into
  structlog `contextvars` for the lifetime of the request. Every log
  line emitted by application code under that request automatically
  carries those fields with no extra plumbing. Context is cleared on
  completion to prevent leakage across requests sharing a worker.

Responses always echo `X-Request-ID` and `traceparent` so callers can
plumb the same ids into their own logs. Audit log rows also capture
`request_id` and `trace_id` for forensic joins against the structured
log stream.

To correlate a user report end to end, ask the caller for the
`X-Request-ID` or `traceparent` returned with their failure, then
`jq 'select(.trace_id == "...")' logs.json` or query your tracing
backend with the same trace id. The audit log can be joined on
`request_id` for any state-changing call.

### Audit log

Every mutating HTTP request (anything that is not `GET`, `HEAD`, or `OPTIONS`)
is recorded to an append-only JSONL file. Read endpoints and the
`/health`, `/ready`, and `/metrics` probes are skipped to keep the log
focused on state-changing actions.

Each line contains:

- `ts` Unix epoch seconds when the request started.
- `actor` `key:<sha256-prefix>` of the supplied `X-API-Key`, or `anonymous`.
  Raw keys are never written to disk.
- `method`, `path`, `status`, `client_ip`, `user_agent`.
- `request_id` the `X-Request-Id` returned to the caller, for cross-log correlation.
- `trace_id` the W3C trace id (32 hex chars) for joining against the structured log stream and any tracing backend.
- `duration_ms` server-side latency.

Configuration via env:

- `CLAWHUM_AUDIT_LOG_PATH` (default `./data/audit.jsonl`)
- `CLAWHUM_AUDIT_ENABLED` (default `true`)
- `CLAWHUM_AUDIT_LOG_PATH` may also be set per-process to redirect output
  (used by the test suite).

The file is append-only and never rotated by the service. Use `logrotate`
or a cron job to ship and truncate it. Example rotation policy:

```
/var/lib/clawhum/data/audit.jsonl {
    daily
    rotate 30
    compress
    missingok
    copytruncate
}
```

Review recent activity:

```bash
tail -n 200 data/audit.jsonl | jq -r '[.ts, .actor, .method, .path, .status] | @tsv'
```

### GDPR data lifecycle

ClawHum exposes two endpoints so a caller can exercise their right of
access and right to erasure without an operator in the loop. Both are
scoped to the caller's API key via the same hashed actor id used by the
audit log.

- `GET /v1/privacy/export` returns every audit event attributed to the
  caller as JSON, plus the resolved actor digest and the friendly
  `api_key_name` from the registry. Feedback rows are not currently
  attributed to a specific key and are noted as such in the response.
- `DELETE /v1/privacy/me` redacts the actor, client IP, user agent, and
  request id on every matching audit row. The append only shape of the
  log is preserved so the forensic timeline (timestamp, method, path,
  status, duration) survives erasure. The endpoint replies with the
  number of events redacted.

The redactor rewrites the audit file via tempfile and `os.replace`, so
a crash mid erasure leaves either the original file or the fully
redacted file, never a half written one. A small subsequent audit row
is written for the `DELETE` call itself; rerun the endpoint to sweep it
if strict tombstoning is required.

Example:

```bash
curl -H "x-api-key: $KEY" https://clawhum.example.com/v1/privacy/export > my-data.json
curl -X DELETE -H "x-api-key: $KEY" https://clawhum.example.com/v1/privacy/me
```

### Error tracking (Sentry)

ClawHum ships with optional Sentry integration. The official `sentry-sdk`
with the FastAPI integration is wired in `clawhum_core.error_tracking` and
initialized during the API lifespan startup. When `CLAWHUM_SENTRY_DSN`
is empty (the default) the helper is a no-op and the SDK is never loaded.

Install the extra in production builds:

```bash
pip install ".[sentry]"
```

Configuration via env:

- `CLAWHUM_SENTRY_DSN` (default empty, disables Sentry).
- `CLAWHUM_SENTRY_ENVIRONMENT` (default `production`).
- `CLAWHUM_SENTRY_TRACES_SAMPLE_RATE` float 0.0 to 1.0, default `0.0`.
- `CLAWHUM_SENTRY_PROFILES_SAMPLE_RATE` float 0.0 to 1.0, default `0.0`.

What the integration does:

- Captures unhandled exceptions raised by FastAPI routes with full stack
  traces, tagged with `release=clawhum@<version>` so issues group per deploy.
- Sets `send_default_pii=false` and runs a `before_send` scrubber that
  filters the `x-api-key`, `Authorization`, `Cookie`, and `Set-Cookie`
  headers before events leave the pod.
- Attaches the ClawHum `request_id` as a Sentry tag when the FastAPI
  scope exposes it, so a Sentry event and a `data/audit.jsonl` row can
  be correlated by id.

In Kubernetes, set the DSN on the Helm chart and it lands in the
release secret alongside the API key:

```bash
helm upgrade --install clawhum infra/helm/clawhum \
  --set sentry.dsn=$SENTRY_DSN \
  --set sentry.environment=prod \
  --set sentry.tracesSampleRate=0.05
```

The deployment reads the DSN from `secretKeyRef` (`sentry_dsn`) rather
than a plain env value, so the secret is never echoed in `kubectl describe`.

### Deploy

- Container image: `infra/docker/Dockerfile` (multi-stage, slim runtime).
- Helm chart: `infra/helm/clawhum/` with `values.yaml` for replica count,
  resource requests and limits, and persistent volume for the index.
- Mount a `PersistentVolume` at `/app/data` so the audit log and FAISS index
  survive pod restarts.

### Health probes

The API exposes four health endpoints with distinct semantics so that
Kubernetes probes behave correctly during slow boots and degraded
states.

- `GET /live` returns `200 {"live": true}` while the event loop is
  responsive. Wired to `livenessProbe`. Slow model load will not get
  the pod killed.
- `GET /startup` returns `200 {"started": true, ...}` only after the
  FastAPI lifespan finishes booting the embedder and loading the index,
  otherwise `503`. Wired to `startupProbe` with a generous failure
  budget (default `failureThreshold: 60`, `periodSeconds: 5`, so up to
  5 minutes to boot the CLAP model).
- `GET /ready` runs real checks (boot complete, embedder constructed,
  index loaded with vector count reported, API key registry resolved)
  and returns `503` with a structured `checks` map if any check fails.
  Wired to `readinessProbe`, so Kubernetes removes the pod from the
  Service endpoint list while the dependency is unhealthy.
- `GET /health` returns a richer status summary for humans and
  dashboards, including embedder class, index backend, track count,
  and vector count.

Tune probe budgets per environment via `values.yaml`:

```yaml
probes:
  startup:
    initialDelaySeconds: 5
    periodSeconds: 5
    failureThreshold: 60
  readiness:
    initialDelaySeconds: 5
    periodSeconds: 10
    failureThreshold: 3
    timeoutSeconds: 3
  liveness:
    initialDelaySeconds: 30
    periodSeconds: 20
    failureThreshold: 3
    timeoutSeconds: 3
```

### Helm hardening

The chart ships with production safety defaults and opt-in knobs for the rest.

On by default:

- Dedicated `ServiceAccount` per release with `automountServiceAccountToken: false`.
- Pod runs as non-root UID/GID `10001`, `fsGroup` `10001`, `seccompProfile: RuntimeDefault`.
- Container has `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`,
  and drops all Linux capabilities. A `tmp` emptyDir is mounted at `/tmp` so
  uvicorn and friends still have a writable scratch dir.
- `PodDisruptionBudget` with `minAvailable: 1` (auto-skipped when
  `replicaCount` is 1).
- Readiness and liveness probes against `/ready` and `/health`.
- Resource requests and limits on every container.

Opt-in (set `--set` or override in your environment values file):

- `autoscaling.enabled=true` provisions an `HorizontalPodAutoscaler` (CPU + memory).
  When enabled, the static `replicas` field is omitted so the HPA owns the count.
  Defaults: min 2, max 10, target 70 percent CPU, 80 percent memory.
- `networkPolicy.enabled=true` provisions a `NetworkPolicy` that:
  - allows ingress only on TCP 7451 from pods or namespaces matching
    `networkPolicy.ingressFromPodLabels` / `ingressFromNamespaceLabels`
    (so wire your ingress controller labels here),
  - always permits egress to kube-dns,
  - permits TCP 443/80 egress to either an explicit `egressCIDRs` allowlist or,
  if none is given, to the public internet only (RFC1918 ranges excluded).

Example production overlay:

```yaml
replicaCount: 3
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 12
networkPolicy:
  enabled: true
  ingressFromNamespaceLabels:
    kubernetes.io/metadata.name: ingress-nginx
  egressCIDRs:
    - 10.20.0.0/16
```

### Scale

- The current rate limiter and audit writer are in-process. For multi-replica
  deployments, front the service with an external rate limiter (NGINX, Envoy,
  or Redis-backed) and ship `data/audit.jsonl` to a central log store rather
  than relying on the local file.
- The FAISS index is loaded into memory at boot. Scale vertically before
  horizontally.

### Backup

- `data/index/` FAISS index and metadata: snapshot the PVC nightly.
- `data/audit.jsonl`: archived by logrotate, also shipped to a SIEM if
  compliance requires.
- `data/feedback.jsonl`: snapshot with the index.

### On-call

- Liveness: `GET /health` returns `200` with version, embedder, and index size.
- Readiness: `GET /ready` returns `{"ready": true}` once the lifespan handler
  has loaded the catalog.
- Metrics: scrape `GET /metrics` from Prometheus.
- Logs: structured JSON via `structlog`, one line per request including the
  `request_id` header.
- For a 5xx burst, grep `data/audit.jsonl` by `status` and `path` to find the
  offending caller and request id.

### Supply chain security

The container image and its dependencies are scanned and signed on every
push to `main`, and the dependency set is rescanned weekly to catch
newly disclosed CVEs even when no code lands.

- `.github/workflows/supply-chain.yml` runs three independent gates:
  `pip-audit` against installed runtime dependencies, `bandit` against
  source under `packages/`, `services/`, and `cli/` (medium severity
  and confidence floor, SARIF uploaded to the GitHub Security tab),
  and a Trivy scan of a freshly built production image for `HIGH` and
  `CRITICAL` OS and language CVEs with `ignore-unfixed` so the gate
  only flags issues we can actually patch. The same job emits an SPDX
  SBOM via `anchore/sbom-action` and uploads it as a build artifact.
- `.github/workflows/docker.yml` publishes the image to GHCR with
  `sbom: true` and `provenance: mode=max` baked into the manifest, then
  signs the resulting digest with `cosign sign --yes` using GitHub OIDC
  for keyless signing (no long-lived keys to rotate; the signature is
  logged in the Rekor public transparency log) and emits a GitHub
  build provenance attestation pinned to the digest.

To verify a pulled image before deploying:

```
DIGEST=$(docker buildx imagetools inspect ghcr.io/<owner>/clawhum:latest --format '{{.Manifest.Digest}}')
cosign verify ghcr.io/<owner>/clawhum@${DIGEST} \
  --certificate-identity-regexp "https://github.com/<owner>/clawhum/.github/workflows/docker.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://ghcr.io/<owner>/clawhum@${DIGEST} --owner <owner>
```

Both commands exit non-zero if the signature, transparency log entry,
or attestation chain cannot be reconstructed back to a GitHub Actions
run on this repository, so a tampered or rebuilt image is rejected
before it ever runs in cluster. Findings from Trivy and Bandit appear
under the repository's Security tab, and the SPDX SBOM artifact can be
fed into downstream license and dependency review tooling.

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
