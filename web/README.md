# clawhum / web

Next.js 15 App Router UI for the clawhum acoustic fingerprint matcher.

## Stack
- Next.js 15 + React 19 RC, App Router
- Tailwind v4 with custom oscilloscope theme (deep black, phosphor green, amber, magenta)
- next/font Inter (chrome) + IBM Plex Mono (numerics, tabular-nums everywhere)
- SWR for polled data, Recharts for sparklines, canvas for waveforms + spectrograms

## Pages
- `/` capture surface (drop file or arm + record), candidates table with per-row spectrogram strip
- `/matches` local query log (dense table, score color-coded by band, latency in ms)
- `/matches/[id]` twin time-aligned waveforms (query top, reference bottom), candidate score bars, per-track spectrograms, feedback submit
- `/catalog` fingerprinted tracks derived from local log, sortable, mini-spectrogram per card
- `/metrics` live `/metrics` scrape, prom text parser, sparklines (counters show +rate/s)
- `/library` stat tiles, `/health` panel, reindex form

## Persistent UI
- Top transport bar / play, pause, stop + peak/rms/lufs meter strip
- Site nav with DAW tab styling

## Dev
```
npm install
npm run dev     # :7452
npm run build
npm test        # exports unit tests via tsx --test
```

## Export
The `/matches` page has an **Export** menu (top right) that downloads the current
filtered query log as either:
- `csv` — flat, one row per candidate, RFC 4180 quoted (open in Excel/Sheets/pandas)
- `json` — nested, one object per query, with the bulky waveform/pitch arrays stripped

Individual queries can be exported from `/matches/[id]` using the inline `csv` / `json`
buttons next to the share action. Files are named `clawhum-matches-YYYYMMDD-HHMMSS.{csv,json}`.

Set `CLAWHUM_API_URL` env to point rewrites at the API (default `http://127.0.0.1:7451`).
The `/api/*` rewrites proxy to `/match`, `/stats`, `/reindex`, `/feedback`, `/metrics`, `/health`.

## Notes
- The API does not persist queries; the matches log + catalog are derived from `localStorage`.
- The reference waveform on `/matches/[id]` is a placeholder visual since the API does not expose
  raw segment audio. The magenta highlight band on the query waveform is aligned to `segment_index`.
- Spectrograms render API-supplied chroma bins when present, otherwise a deterministic
  seeded noise placeholder (labelled "sample" via the empty `bins` prop).
