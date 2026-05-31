# ClawHum

Query-by-humming. Hum a melody or upload a clip, get ranked matches from a local library or Spotify catalog.

![landing](docs/screenshots/landing.png)

## What it does

Accepts an audio upload (hum, whistle, recorded clip), decodes it via `soundfile`/`librosa`, and runs it through a DSP pre-processing chain (butterworth biquad band-pass, pre-emphasis at 0.97, optional VAD trim). The cleaned signal is segmented into 6 s windows and embedded with CLAP (`laion/clap-htsat-unfused`) when ML extras are installed, or with a deterministic MFCC + chroma + spectral-contrast hash embedder as fallback. Embeddings are searched against a FAISS HNSW index (or a NumPy brute-force index on Apple Silicon where `faiss-cpu` is unavailable) and reranked by tempo proximity. Results stream back as scored track candidates with previews and artwork. A Prometheus `/metrics` endpoint and structured logs expose request volume, match counts, and index size.

ClawHum is a query-by-humming engine that turns a microphone clip into ranked song matches against a local or Spotify-backed catalog.

## Try it (saved history views)

`http://127.0.0.1:7452/history` now lets you pin any combination of search query, tag, sort, and starred-only filter as a named view. The chips appear above the filter bar; click one to apply, hover to rename or delete. Views are server-scoped per API key so they survive device switches the same way history does, and the route is mounted at `/v1/history/views` as part of the stable public surface.

```bash
# save a view of "starred jazz, best score first"
curl -s -X POST -H "X-API-Key: $CLAWHUM_API_KEY" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:7451/v1/history/views \
  -d '{"name":"Top jazz","filters":{"q":"","tag":"jazz","sort":"top_score","starred":true}}'
# list them later
curl -s -H "X-API-Key: $CLAWHUM_API_KEY" http://127.0.0.1:7451/v1/history/views
```

## Try it (collections of saved matches)

`http://127.0.0.1:7452/collections` lets you bundle saved history rows into a named, shareable set, like a tiny playlist of your top humming guesses. Pick rows from a checkbox list of your latest 50 saved matches, give the collection a title and optional note, and one click creates a public URL at `/c/<id>` that anyone can open in incognito without an API key. The owner-only list view supports copy-link, open in a new tab, and delete. Storage is tenant-scoped JSONL (same pattern as `/share` and `/webhooks`), public reads are unauthenticated, writes require your API key, and the route is also mounted on `/v1/collections` as part of the stable public surface.

```bash
# create a collection from two history rows
curl -s -X POST -H "X-API-Key: $CLAWHUM_API_KEY" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:7451/v1/collections \
  -d '{"title":"top humming guesses","note":"weekend picks","items":[{"label":"first try","results":[{"track_id":"t1","title":"Take On Me","artist":"a-ha","score":0.91,"segment_index":0}],"query_id":"q1","elapsed_ms":42}]}'
# the response.url_path is your public link: /c/<id>
```

Shared collection links now ship a 1200x630 Open Graph preview image rendered
on demand at `/c/<id>/opengraph-image`, so pasting a collection URL into
Slack, iMessage, Twitter, or LinkedIn unfurls into a card with the title,
item count, top match, and a preview of the first few items. The image
generates from live data (no snapshotting), respects the same public
access rules as the page, and matches the existing `/r/<id>` share card.

## Try it (developers, v1 API): every public endpoint, copy-paste curl/python/JS pre-filled with your stored API key, and a live `try /v1/me` button that calls the backend through the same proxy a real client would use. The same FastAPI routers (match, batch, share, history, usage, me, webhooks, library stats) are now mounted twice, once at their original path for the in-app UI and once under `/v1` as the stable public surface we promise not to break. The web app forwards `/api/v1/*` straight through to the backend `/v1/*`.

```bash
# version-pinned match endpoint, same shape as /match
curl -X POST http://127.0.0.1:7452/api/v1/match \
  -H "X-API-Key: $CLAWHUM_API_KEY" \
  -F "audio=@hum.wav" -F "top_k=5"
```

## Try it (activity inbox)

`http://127.0.0.1:7452/activity` is a single chronological feed of every saved match and every webhook delivery on your account, tenant-scoped on the server side. New items since your last visit light an unread dot on the nav tab; opening the page clears it. Filter by kind (match or delivery) or by free text. The backend route is `GET /activity?limit=&since=&kind=` and returns `{items, total, latest_at}` so the client can store a cursor without polling individual subsystems.

```bash
curl -s -H "X-API-Key: $CLAWHUM_API_KEY" \
  'http://127.0.0.1:7451/activity?limit=20' | jq '.items[] | {kind, title, ok, created_at}'
```

## Try it (bulk select on history)

`http://127.0.0.1:7452/history` now supports multi-select with a sticky action bar. Click the square on any row, or hit the header checkbox to flip every entry on the current page. Selecting at least one row reveals two bulk actions: `tag` opens an inline input that merges a comma-separated list onto every selected row (lowercased, deduped, sorted), and `delete` fans out parallel `DELETE /api/history/{id}` calls with a confirm guard and a failure counter. Selections survive pagination and tag-filter changes for rows that stay visible, and are pruned automatically when a row drops out of view. Pure selection helpers live in `web/lib/bulkSelect.ts` with unit tests in `web/tests/bulkSelect.test.ts`.

```bash
# the bulk delete UI calls the same per-id endpoint
curl -s -X DELETE -H "X-API-Key: $CLAWHUM_API_KEY" \
  http://127.0.0.1:7451/history/hst_abc123
```

## Try it (share from history)

Every row on `http://127.0.0.1:7452/history` now has an inline share button. Clicking it creates a public link by calling `POST /share` with the row's existing match payload, copies `https://<host>/r/<id>` to the clipboard, and toasts the URL with an `open` action so you can verify it in a new tab. The row's display name is sent through as the share note so the public page has context. The shared link is the same `/r/<id>` route that already renders an OpenGraph image, so dropping it into Slack or iMessage previews the top match without any extra round trip. Pure adapters live in `web/lib/share.ts` with unit tests in `web/tests/share.test.ts`.

```bash
# create a share link from any saved history row
curl -s -X POST -H "X-API-Key: $CLAWHUM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query_id":"q_abc","elapsed_ms":400,"count":1,"results":[{"track_id":"trk_1","title":"First Light","artist":"Aurora","score":0.92,"segment_index":0,"source":"library"}],"filename":"hum.wav","duration_sec":4.2}' \
  http://127.0.0.1:7451/share | jq .url_path
```

## Try it (privacy controls in settings)

ClawHum now exposes the GDPR data lifecycle endpoints directly inside `http://127.0.0.1:7452/settings` under a new "privacy & data" panel. `download json` calls `GET /v1/privacy/export` with the saved API key, summarises the audit and feedback row counts inline, and saves a timestamped `clawhum-export-YYYYMMDD-HHMMSS.json` to the browser. `erase my data` asks the user to type `ERASE` to confirm, then issues `DELETE /v1/privacy/me`, redacts every audit event and feedback row tied to the caller's actor id, and reports the counts back in the UI. Loading, error, and success states each have their own rendered shape and the destructive button stays disabled until the confirmation token matches. Pure helpers live in `web/lib/privacy.ts` with unit tests in `web/tests/privacy.test.ts`.

```bash
# export every audit + feedback row tied to your key
curl -s -H "X-API-Key: $CLAWHUM_API_KEY" \
  http://127.0.0.1:7451/v1/privacy/export | jq '.audit_event_count, .feedback_row_count'

# redact them (cannot be undone)
curl -s -X DELETE -H "X-API-Key: $CLAWHUM_API_KEY" \
  http://127.0.0.1:7451/v1/privacy/me
```

## Try it (in-app notifications)

ClawHum now has a global toast notification system so you find out the moment a long-running action finishes, even if you switched tabs. A match completion toasts the top hit with a one-click `open details` action that jumps straight to `/matches/<query_id>`. Batch jobs toast `batch complete` with the ok/failed counts as soon as the server returns. Share links toast the copied URL with an `open` action. Errors stick around longer than successes so they survive a glance away from the screen. The toast region is rendered with an ARIA live region so screen readers announce it, and the stack caps at 5 so the UI never gets buried. The whole thing is dependency free and lives in `web/lib/toast.ts` plus `web/components/Toaster.tsx`, mounted once in the root layout.

```bash
# unit tests for the toast store
cd web && npx tsx --test tests/toast.test.ts
```

## Try it (pricing page)

ClawHum now ships a real pricing page at `http://127.0.0.1:7452/pricing` with three tiers (Free, Studio, Label), monthly quotas, a feature matrix, and an accessible FAQ. The free plan CTA drops users straight into the capture page. Paid CTAs open Stripe Payment Links when `NEXT_PUBLIC_STRIPE_LINK_STUDIO` / `NEXT_PUBLIC_STRIPE_LINK_LABEL` are set at build time, and fall back to a real `mailto:` so customers can always reach you. The existing usage page "upgrade" buttons now route here instead of dead ending in settings.

```bash
# preview the page
curl -s http://127.0.0.1:7452/pricing | grep -i '"clawhum / pricing"'
```

## Try it (install to your phone)

ClawHum is now an installable PWA. Open `http://127.0.0.1:7452/` on Chrome / Edge (desktop or Android) and tap the install banner that appears in the bottom right, or use the browser menu → Install. On iOS Safari use Share → Add to Home Screen. The service worker precaches an offline shell so navigating to a stale tab without network shows a useful `/offline` page that still lets you read your saved history. Live matching always hits the network because the fingerprint index lives on the server, so `/api/*` is never cached.

```bash
# verify the manifest and service worker ship with the build
curl -s http://127.0.0.1:7452/manifest.webmanifest | head -5
curl -sI http://127.0.0.1:7452/sw.js | head -3
```

## Try it (usage + quota meter)

ClawHum now tracks per-tenant chargeable activity (match, batch, pitch, share, history, webhook) and exposes a live quota meter at `http://127.0.0.1:7452/usage`. The page shows the rolling minute / day / month totals, a 30 day daily sparkline, a per-event breakdown, and an upgrade CTA that fires when you cross 80% of the free monthly quota. The default free quota is 1000 requests / 30 days; override with `CLAWHUM_FREE_QUOTA_MONTH=5000`.

```bash
# inspect your own usage
curl http://127.0.0.1:7451/usage -H 'X-API-Key: dev'
```

Events are recorded by middleware on every 2xx response to a billable route and persisted to `data/usage.jsonl` (override with `CLAWHUM_USAGE_PATH`). Tenant scoping means each API key only sees its own counters.

## Try it (star + sort cloud history)

Every saved hum on `http://127.0.0.1:7452/history` now has a star toggle and the toolbar gains a sort dropdown plus a starred-only filter. Star anything you want to come back to and pin the page to `?starred=true` to keep the noise down. Sort by newest, oldest, name, most matches, or best score. Filters compose with search, tag, pagination, and export, so `export` always downloads exactly what you see. State persists per record on the server so it follows you across devices.

```bash
# list only starred runs, sorted by best top score
curl 'http://127.0.0.1:7451/history?starred=true&sort=top_score' -H 'X-API-Key: dev'

# star a saved run
curl -X PATCH http://127.0.0.1:7451/history/<id> \
  -H 'X-API-Key: dev' -H 'Content-Type: application/json' \
  -d '{"starred":true}'

# export starred only to csv
curl 'http://127.0.0.1:7451/history/export?format=csv&starred=true' -H 'X-API-Key: dev' -o starred.csv
```

## Try it (cloud history)

ClawHum now syncs every match to your account so history survives device switches and browser-storage wipes. Open `http://127.0.0.1:7452/history` after setting an API key in `/settings` and you'll see every run, searchable by query name, filename, artist, or title, with rename, tag, and delete inline. The `/matches` page keeps the local-only log for offline use.

```bash
# save a match to your history
curl -X POST http://127.0.0.1:7451/history \
  -H 'X-API-Key: dev' -H 'Content-Type: application/json' \
  -d '{"query_id":"q-1","elapsed_ms":42,"count":1,"name":"verse hook","tags":["practice"],"results":[{"track_id":"t1","title":"Bohemian Rhapsody","artist":"Queen","score":0.91}]}'

# list (newest first, with search + tag filter)
curl 'http://127.0.0.1:7451/history?q=queen&limit=10' -H 'X-API-Key: dev'

# rename / retag
curl -X PATCH http://127.0.0.1:7451/history/<id> \
  -H 'X-API-Key: dev' -H 'Content-Type: application/json' \
  -d '{"name":"chorus hook","tags":["jazz","demo"]}'

# delete
curl -X DELETE http://127.0.0.1:7451/history/<id> -H 'X-API-Key: dev'
```

Entries are tenant-scoped at write time so each API key only sees its own history.

## Try it (export your full history)

Download your entire saved history as CSV or JSON, respecting the search and tag filters currently active on `/history`. The CSV flattens to one row per candidate match so it drops straight into a spreadsheet; the JSON is the full nested payload for programmatic re-ingestion.

Open `http://127.0.0.1:7452/history`, set any filter you want, click **export**, then pick CSV or JSON. From the API:

```sh
# full history as CSV
curl -L 'http://127.0.0.1:7451/history/export?format=csv' \
  -H 'X-API-Key: dev' -o history.csv

# only entries tagged "jazz", as JSON
curl -L 'http://127.0.0.1:7451/history/export?format=json&tag=jazz' \
  -H 'X-API-Key: dev' -o history.json
```

Server-side coverage lives in `tests/integration/test_history.py::test_history_export_csv_and_json`.

## Try it (webhooks)

ClawHum now ships outbound webhooks. Register a URL at `http://127.0.0.1:7452/webhooks` and every completed match POSTs the full `MatchResponse` JSON to that endpoint, signed with HMAC-SHA256 in the `X-Clawhum-Signature` header. Failed deliveries retry with exponential backoff up to three attempts, and every attempt is recorded in a per-webhook delivery log you can inspect from the same page.

Every registered endpoint now also gets a one-click **send test** button that fires a synthetic `webhook.test` payload to the URL immediately so you can verify reachability before a real event ever happens. Each row in the delivery log carries a **redeliver** action that replays the original payload (same event, same bytes) so you can recover from a downstream outage without humming the same melody again. Test pings are not replayable on purpose: the log marks them with `replayable: false` so the UI keeps the button honest.

```bash
# register an endpoint
curl -X POST http://127.0.0.1:7451/webhooks \
  -H 'X-API-Key: dev' -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/hooks/clawhum","events":["match.completed"]}'
# -> {"id":"...","secret":"whsec_...","events":["match.completed"], ...}

# fire a synthetic test ping (logged like any real delivery)
curl -X POST http://127.0.0.1:7451/webhooks/<id>/test -H 'X-API-Key: dev'
# -> {"ok":true,"delivery_id":"...","event":"webhook.test"}

# replay a past delivery (same payload, new attempt counter)
curl -X POST http://127.0.0.1:7451/webhooks/<id>/deliveries/<delivery_id>/redeliver \
  -H 'X-API-Key: dev'

# list and inspect
curl http://127.0.0.1:7451/webhooks -H 'X-API-Key: dev'
curl http://127.0.0.1:7451/webhooks/<id>/deliveries -H 'X-API-Key: dev'
```

Verify the signature on your receiver (Node):

```ts
import crypto from "node:crypto";
const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
const ok = crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(req.headers["x-clawhum-signature"]));
```

The webhook store is JSONL on disk (tenant scoped on read and delete) so it follows the same operational pattern as `/share` and `/feedback`; no new infrastructure required.

## Try it (history)

The query log at `/matches` is now a full local CRM for your hums: every capture is saved in the browser, and you can search, filter, tag, rename, and delete entries without leaving the page. Open `http://127.0.0.1:7452/matches` after a few captures and try:

- Free-text search across query id, custom name, filename, track title, artist, and tag.
- Time-range presets (24h / 7d / 30d / all time) and a minimum best-score slider.
- Tag chips with frequency counts, click to ANY-of filter.
- Sort by newest, oldest, score, or latency.
- Inline rename and tag editor on every row (keyboard: enter to save, escape to cancel).
- Per-row delete plus pagination (25 per page).
- CSV / JSON export honours the current filter set, so you can hand a teammate exactly the slice they asked for.

The pure filter / sort / tag helpers live in `web/lib/history.ts` and are covered by `web/tests/history.test.ts` (run `pnpm test` from `web/`).

## Try it (settings + api key)

ClawHum now has a Settings page at `http://127.0.0.1:7452/settings` where you paste an API key once and every subsequent `/api/*` call from the browser carries it as `X-API-Key`. The page also calls a new `/me` endpoint and shows:

- Resolved tenant id, key name, and role set.
- Configured per-minute rate limit, plus a live session usage meter.
- A copy-paste curl example pre-filled with your stored key.

The key stays in `localStorage` on that device, never gets synced anywhere, and a one-click *clear* button wipes it. Probe the new endpoint directly:

```bash
curl http://127.0.0.1:7451/me -H 'x-api-key: dev'
# -> {"tenant_id":"dev","key_name":"dev","roles":[...],
#     "rate_limit_per_minute":120,"auth_mode":"open","masked_key":"dev"}
```

Backed by `tests/integration/test_me.py` (open mode, valid key, and 401 on missing key in key-required mode).

## Try it (batch)

Upload a `.zip` of audio clips and get one results file back. The whole archive runs through the same matcher as single-shot `/match`, with per-clip top-k matches, per-clip errors that do not fail the batch, and both JSON and CSV output. Visit `http://127.0.0.1:7452/batch`, drop a zip, and pick the format you want, or call it directly:

```bash
# zip up a folder of hums
zip -j hums.zip ./recordings/*.wav

# JSON output (inline results)
curl -X POST http://127.0.0.1:7451/batch \
     -H 'x-api-key: dev' \
     -F 'archive=@hums.zip' \
     -F 'top_k=3' \
     -F 'format=json'

# CSV download (one row per match, attachment headers set)
curl -X POST http://127.0.0.1:7451/batch \
     -H 'x-api-key: dev' \
     -F 'archive=@hums.zip' \
     -F 'format=csv' \
     -o results.csv
```

Caps: 200 MiB per archive, 100 clips per batch, 50 MiB per clip. Bad codecs and oversize entries surface as per-row `error` fields so a single broken file does not fail the rest of the run.

## Try it (share)

Share any match result with one click. Open any item in `/matches`, hit *share* in the top strip, and a public read-only URL is copied to your clipboard. The link works in an incognito window without an API key:

```bash
# create a share record (writer role required)
curl -X POST http://127.0.0.1:7451/share \
     -H 'content-type: application/json' \
     -H 'x-api-key: dev' \
     -d '{"query_id":"q-abc","elapsed_ms":42,"count":1,
          "results":[{"track_id":"t1","title":"Test Song",
                      "artist":"Tester","score":0.91,"segment_index":0}]}'
# -> {"id":"a1b2c3d4e5f6","url_path":"/r/a1b2c3d4e5f6"}

# read it publicly (no auth)
curl http://127.0.0.1:7451/share/a1b2c3d4e5f6
```

Open `http://127.0.0.1:7452/r/<id>` to see the rendered page with ranked candidates, latency, and OG metadata for link previews. Records are appended to `CLAWHUM_SHARES_PATH` (defaults to `./data/shares.jsonl`).

Every share URL also serves a real 1200x630 social preview card at `http://127.0.0.1:7452/r/<id>/opengraph-image`, generated on demand with `next/og`. Paste a `/r/<id>` link into Slack, iMessage, Twitter, or Discord and it renders the top match, artist, score, latency, and three runner-up candidates. The card is read straight off the shared payload, so it stays in sync with whatever was shared and never serves stale data.

### Manage your share links

Every share you create is listed on `http://127.0.0.1:7452/shares`. The page is tenant scoped (signed in with your API key), supports inline search across id, title, artist, filename, and note, and lets you copy a fresh link or revoke any share with one click. Revoked share ids return 404 on `/r/<id>` immediately and disappear from the listing. Behind the scenes:

```bash
# list your shares
curl -H "X-API-Key: $CLAWHUM_KEY" http://127.0.0.1:7451/share

# revoke a share (writes a tombstone so future GETs return 404)
curl -X DELETE -H "X-API-Key: $CLAWHUM_KEY" http://127.0.0.1:7451/share/a1b2c3d4e5f6
```

A different tenant cannot list or revoke your shares; the API answers 404 to keep existence private.

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
- `/demo` route with three public-domain hum samples (Twinkle, Ode to Joy, Frere Jacques) that POST to the real `/match` endpoint and render top-k results with latency and score bars. Visit `http://127.0.0.1:7452/demo` and click *match*; or hit the API directly:
  ```
  curl -F audio=@web/public/samples/twinkle.wav -F top_k=10 -F threshold=0.0 \
       -H 'x-api-key: dev' http://127.0.0.1:7451/match
  ```
- `GET /track/{track_id}/audio` streams the reference audio for any track in the in-memory catalogue. The match detail page (`/matches/[id]`) uses this to decode the matched track in the browser, render its real waveform next to the captured query, and offer an A/B player so a user can hear why the score is what it is. Files are confined to `CLAWHUM_LIBRARY_PATH`; unknown ids return 404, paths outside the library root return 403. Try it:
  ```
  curl -H 'x-api-key: dev' -o ref.wav \
       http://127.0.0.1:7451/track/local:abc1234567890abc/audio
  ```
- `POST /pitch` and `GET /track/{track_id}/pitch` extract a pYIN fundamental-frequency contour (Hz + MIDI, voiced ratio, median Hz) from an upload or from any indexed track's matched segment. The match detail page overlays the user's query melody against the reference segment on a single recharts plot, key-normalised to the same median MIDI, with a per-frame agreement percentage so a user can see exactly *where* the two melodies tracked together and where they diverged. Returns at most 240 points per contour to keep payloads small. Try it:
  ```
  curl -F audio=@web/public/samples/twinkle.wav -H 'x-api-key: dev' \
       http://127.0.0.1:7451/pitch
  ```
- 8 locale files under `web/i18n/` (de, en, es, fr, it, ja, ko, pt).
- `GET /feedback` returns the current tenant's confirm / reject votes with a small summary (counts, unique queries, unique tracks) and supports `vote`, `track_id`, `limit`, and `offset` filters. The rebuilt `/feedback` web page reads it as a real review queue: vote-distribution bar chart, filterable table linking back to each match detail page, and an *export triplets* button that emits anchor / positive / negative pairs as JSONL ready for triplet-loss fine-tuning of the embedder. Visit `http://127.0.0.1:7452/feedback` after voting on a few matches, or hit the API directly:
  ```
  curl -H 'x-api-key: dev' 'http://127.0.0.1:7451/feedback?vote=1&limit=50'
  ```
- `/insights` route renders a local-only analytics dashboard over your query log: KPI tiles (hit rate, strong-hit rate, mean top score, latency p95, total audio sent), a top-score histogram, a latency histogram, an activity-over-time area chart, and a most-matched-tracks leaderboard. Powered entirely by `localStorage` (`clawhum.matches.v1`), so it works without any backend round-trip and reveals nothing to the API. Visit `http://127.0.0.1:7452/insights` after running a few captures or the `/demo` samples.
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

Exposed at `GET /metrics` (text/plain, Prometheus exposition format,
produced by `prometheus_client`):

- `clawhum_uptime_seconds` (gauge) seconds since the API started.
- `clawhum_index_vectors` (gauge) vectors loaded in the search index.
- `clawhum_index_tracks` (gauge) tracks loaded in library metadata.
- `clawhum_http_requests_total{method,route,status}` (counter)
  every HTTP request handled, labelled by HTTP method, the matched
  FastAPI route path template, and the response status code. The
  `route` label uses the templated path (for example `/v1/match`)
  rather than the raw URL so Prometheus cardinality stays bounded
  when path parameters are present.
- `clawhum_http_request_duration_seconds{method,route}` (histogram)
  request latency in seconds with buckets tuned for a mix of
  sub-millisecond health checks and multi-second match calls.

The `/metrics` endpoint itself is excluded from request metrics so
Prom scrapes do not pollute the signal. Domain gauges are evaluated
lazily by a custom collector at scrape time, so each scrape reflects
the current process state without a background updater.

Example PromQL:

- p95 latency on the match route over 5 minutes:
  `histogram_quantile(0.95, sum by (le) (rate(clawhum_http_request_duration_seconds_bucket{route="/v1/match"}[5m])))`
- request error rate per route over 5 minutes:
  `sum by (route) (rate(clawhum_http_requests_total{status=~"5.."}[5m]))`
- queries per second by tenant via the structured access log
  (tenant is bound into structlog contextvars rather than into the
  Prometheus label set to keep cardinality low).

## Operations

Day-2 operational concerns for running ClawHum in a shared or production environment.

### Container runtime hardening

Both `infra/docker/Dockerfile` (CPU) and `infra/docker/Dockerfile.cuda`
(GPU) build images that run as the dedicated non-root user `clawhum`
(uid 10001, gid 10001). The uid matches the uid the Helm chart pins in
`podSecurityContext.runAsUser`, so the chart's defaults (`runAsNonRoot:
true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation:
false`, all capabilities dropped) work without overrides.

The image pre-creates `/app/data` owned by uid 10001 so the container
starts cleanly under `docker run` even when no PVC is mounted; in the
chart that path is backed by a PVC or `emptyDir` and `/tmp` is an
`emptyDir` mount, which are the only writable paths the app needs.

`tini` is installed as PID 1 via `ENTRYPOINT ["/usr/bin/tini", "--"]`
so `SIGTERM` from Kubernetes is forwarded to uvicorn for graceful
shutdown within the pod's `terminationGracePeriodSeconds`.

If you bump `runAsUser` in `infra/helm/clawhum/values.yaml` you must
rebuild the image with the matching uid. `tests/unit/test_container_security.py`
fails fast on that drift in CI.

### CORS and HTTP security headers

The API ships defense-in-depth HTTP headers on every response and
treats CORS as an explicit allowlist rather than a wildcard. Defaults
live in `packages/core/clawhum_core/settings.py` and are wired in
`services/api/clawhum_api/app.py`; all of them are configurable through
`CLAWHUM_*` environment variables (see `.env.example`).

Baseline response headers, emitted by `SecurityHeadersMiddleware`:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`
- `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`
  since the service returns JSON, not HTML
- `Cross-Origin-Opener-Policy: same-origin` and
  `Cross-Origin-Resource-Policy: same-origin`
- `Strict-Transport-Security: max-age=63072000; includeSubDomains`
  is added only when the request was served over HTTPS or arrived
  through a proxy that set `X-Forwarded-Proto: https`. Local
  `http://127.0.0.1` development never pins HSTS, so cleartext dev does
  not lock browsers out of the production hostname later.

Disable the whole middleware with `CLAWHUM_SECURITY_HEADERS_ENABLED=false`
if you front the API with an ingress that owns these headers already.

CORS is configured per environment:

```
CLAWHUM_CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com
CLAWHUM_CORS_ALLOW_CREDENTIALS=true
CLAWHUM_CORS_ALLOW_METHODS=GET,POST,PUT,PATCH,DELETE,OPTIONS
CLAWHUM_CORS_ALLOW_HEADERS=Authorization,Content-Type,X-API-Key,X-Request-ID,traceparent
```

The default value of `CLAWHUM_CORS_ALLOW_ORIGINS=*` stays for local
dev only. When origins are pinned to an explicit list,
`CLAWHUM_CORS_ALLOW_CREDENTIALS=true` is honored and the middleware
echoes the matching origin back. When origins are `*`, credentials are
forced off regardless of the setting because browsers reject
credentialed responses paired with a wildcard origin. Request ids and
rate-limit headers are added to `expose_headers` so browser clients can
read them for correlation and backoff.

Regression tests live in `tests/integration/test_security_headers.py`
and cover the default headers, HSTS gating on `X-Forwarded-Proto`,
the disabled toggle, wildcard-blocks-credentials, and explicit origin
matching for preflights.

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
- `CLAWHUM_AUDIT_MAX_BYTES` (default `52428800`, 50 MiB) rotate the
  active file when it exceeds this size. Set to `0` to disable in
  process rotation and delegate to logrotate or a sidecar.
- `CLAWHUM_AUDIT_BACKUP_COUNT` (default `5`) maximum number of
  rotated files kept on disk. Older files are deleted.
- `CLAWHUM_AUDIT_LOG_PATH` may also be set per-process to redirect output
  (used by the test suite).

Rotation runs in process: when the active file passes the threshold,
it is renamed `audit.jsonl.1`, existing `.N` files shift to `.N+1`,
and anything past `CLAWHUM_AUDIT_BACKUP_COUNT` is deleted. The rename
uses `os.replace` so the file pointer swap is atomic on POSIX. The
export and erasure endpoints walk every rotated sibling, so GDPR
requests still see the full retained history.

If you prefer an external rotator (logrotate, fluent-bit, sidecar),
set `CLAWHUM_AUDIT_MAX_BYTES=0` and use a policy like:

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
