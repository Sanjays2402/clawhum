// Export utilities for the query log. Pure functions, no I/O, easy to test.
import type { StoredMatch } from "./history";

/**
 * Escape a field for RFC 4180 CSV. Wraps in quotes and doubles internal quotes
 * whenever the value contains a comma, quote, CR, or LF. Null/undefined become "".
 */
export function csvField(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (s === "") return "";
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export interface FlatRow {
  query_id: string;
  timestamp_iso: string;
  query_filename: string;
  query_duration_sec: number | "";
  candidate_rank: number;
  track_id: string;
  title: string;
  artist: string;
  album: string;
  source: string;
  score: number;
  segment_index: number;
  tempo_bpm: number | "";
  preview_url: string;
  artwork_url: string;
  elapsed_ms: number;
  candidate_count: number;
}

export const FLAT_COLUMNS: (keyof FlatRow)[] = [
  "query_id",
  "timestamp_iso",
  "query_filename",
  "query_duration_sec",
  "candidate_rank",
  "track_id",
  "title",
  "artist",
  "album",
  "source",
  "score",
  "segment_index",
  "tempo_bpm",
  "preview_url",
  "artwork_url",
  "elapsed_ms",
  "candidate_count",
];

/** Flatten a query log so each candidate becomes its own row (one query without results emits a single placeholder row). */
export function flattenMatches(matches: StoredMatch[]): FlatRow[] {
  const out: FlatRow[] = [];
  for (const m of matches) {
    const ts = new Date(m.ts).toISOString();
    if (!m.results || m.results.length === 0) {
      out.push({
        query_id: m.query_id,
        timestamp_iso: ts,
        query_filename: m.filename ?? "",
        query_duration_sec: m.duration_sec ?? "",
        candidate_rank: 0,
        track_id: "",
        title: "",
        artist: "",
        album: "",
        source: "",
        score: 0,
        segment_index: 0,
        tempo_bpm: "",
        preview_url: "",
        artwork_url: "",
        elapsed_ms: m.elapsed_ms,
        candidate_count: 0,
      });
      continue;
    }
    m.results.forEach((r, i) => {
      out.push({
        query_id: m.query_id,
        timestamp_iso: ts,
        query_filename: m.filename ?? "",
        query_duration_sec: m.duration_sec ?? "",
        candidate_rank: i + 1,
        track_id: r.track_id,
        title: r.title,
        artist: r.artist,
        album: r.album ?? "",
        source: r.source,
        score: r.score,
        segment_index: r.segment_index,
        tempo_bpm: r.tempo_bpm ?? "",
        preview_url: r.preview_url ?? "",
        artwork_url: r.artwork_url ?? "",
        elapsed_ms: m.elapsed_ms,
        candidate_count: m.count,
      });
    });
  }
  return out;
}

export function rowsToCsv(rows: FlatRow[]): string {
  const header = FLAT_COLUMNS.join(",");
  const lines = rows.map(r => FLAT_COLUMNS.map(c => csvField(r[c])).join(","));
  return [header, ...lines].join("\r\n") + "\r\n";
}

/** Build a JSON payload that strips bulky waveform/pitch arrays so downloads stay small and shareable. */
export function buildJsonExport(matches: StoredMatch[]) {
  return {
    exported_at: new Date().toISOString(),
    schema: "clawhum.matches.export.v1",
    count: matches.length,
    matches: matches.map(m => ({
      query_id: m.query_id,
      ts: m.ts,
      timestamp_iso: new Date(m.ts).toISOString(),
      elapsed_ms: m.elapsed_ms,
      count: m.count,
      filename: m.filename ?? null,
      duration_sec: m.duration_sec ?? null,
      results: m.results,
    })),
  };
}

export function timestampSlug(d: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

/** Trigger a browser download for a string blob. Safe to call only on the client. */
export function downloadBlob(filename: string, mime: string, body: string): void {
  if (typeof window === "undefined") return;
  const blob = new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
