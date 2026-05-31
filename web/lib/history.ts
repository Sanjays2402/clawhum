// Client-side persisted match history (the API doesn't store queries).
import type { MatchResponse, MatchResult } from "./api";

export interface StoredMatch {
  query_id: string;
  ts: number;             // ms epoch
  elapsed_ms: number;
  count: number;
  filename?: string;
  duration_sec?: number;
  /** User-supplied display name for the query (rename). */
  name?: string;
  /** User-supplied tags. Lowercased, deduped. */
  tags?: string[];
  /** Downsampled Float32Array of the query waveform (~4096 samples max) */
  query_waveform?: number[];
  /** Optional pre-computed pitch contour of the query, for explainability views. */
  query_pitch?: {
    sr: number;
    duration_sec: number;
    hop_sec: number;
    times: number[];
    hz: (number | null)[];
    midi: (number | null)[];
    voiced_ratio: number;
    median_hz: number;
  };
  results: MatchResult[];
}

const KEY = "clawhum.matches.v1";
const MAX = 200;

export function loadMatches(): StoredMatch[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as StoredMatch[];
    if (!Array.isArray(arr)) return [];
    return arr;
  } catch {
    return [];
  }
}

export function saveMatch(m: StoredMatch) {
  if (typeof window === "undefined") return;
  const all = loadMatches();
  all.unshift(m);
  while (all.length > MAX) all.pop();
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // quota: drop waveform and retry
    for (const e of all) delete e.query_waveform;
    try { localStorage.setItem(KEY, JSON.stringify(all)); } catch {}
  }
}

export function getMatch(id: string): StoredMatch | null {
  return loadMatches().find(m => m.query_id === id) ?? null;
}

export function clearMatches() {
  if (typeof window !== "undefined") localStorage.removeItem(KEY);
}

/** Delete a single match by query_id. Returns true if it existed. */
export function deleteMatch(query_id: string): boolean {
  if (typeof window === "undefined") return false;
  const all = loadMatches();
  const next = all.filter(m => m.query_id !== query_id);
  if (next.length === all.length) return false;
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch {}
  return true;
}

/** Patch a stored match by query_id. Returns the new record, or null if not found. */
export function updateMatch(
  query_id: string,
  patch: Partial<Pick<StoredMatch, "name" | "tags">>,
): StoredMatch | null {
  if (typeof window === "undefined") return null;
  const all = loadMatches();
  const i = all.findIndex(m => m.query_id === query_id);
  if (i < 0) return null;
  const cur = all[i];
  const next: StoredMatch = { ...cur };
  if ("name" in patch) {
    const v = patch.name?.trim();
    if (v) next.name = v; else delete next.name;
  }
  if ("tags" in patch) {
    next.tags = normaliseTags(patch.tags ?? []);
  }
  all[i] = next;
  try { localStorage.setItem(KEY, JSON.stringify(all)); } catch {}
  return next;
}

/** Lowercase, trim, dedupe, drop empties. Stable order. */
export function normaliseTags(tags: readonly (string | undefined | null)[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of tags) {
    if (!raw) continue;
    const v = String(raw).trim().toLowerCase().replace(/\s+/g, "-").slice(0, 32);
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

/** Distinct tags across all stored matches, sorted by frequency desc then alpha. */
export function allTags(matches: readonly StoredMatch[]): { tag: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const m of matches) {
    for (const t of m.tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

export interface MatchFilter {
  /** Free-text query: id, name, filename, tag, track title, artist. */
  q?: string;
  /** ANY-of tag filter. */
  tags?: readonly string[];
  /** Inclusive lower bound on best candidate score (0..1). */
  minScore?: number;
  /** Inclusive lower bound on ts (ms epoch). */
  since?: number;
  /** Exclusive upper bound on ts (ms epoch). */
  until?: number;
}

export type MatchSort =
  | "ts_desc" | "ts_asc"
  | "score_desc" | "score_asc"
  | "latency_asc" | "latency_desc";

export function filterMatches(matches: readonly StoredMatch[], f: MatchFilter): StoredMatch[] {
  const q = f.q?.trim().toLowerCase() ?? "";
  const tags = f.tags && f.tags.length ? new Set(f.tags) : null;
  const minScore = f.minScore ?? 0;
  const since = f.since ?? 0;
  const until = f.until ?? Number.POSITIVE_INFINITY;
  return matches.filter(m => {
    if (m.ts < since || m.ts >= until) return false;
    const best = m.results[0]?.score ?? 0;
    if (best < minScore) return false;
    if (tags) {
      const mtags = m.tags ?? [];
      let any = false;
      for (const t of mtags) { if (tags.has(t)) { any = true; break; } }
      if (!any) return false;
    }
    if (!q) return true;
    if (m.query_id.toLowerCase().includes(q)) return true;
    if (m.name?.toLowerCase().includes(q)) return true;
    if (m.filename?.toLowerCase().includes(q)) return true;
    if ((m.tags ?? []).some(t => t.includes(q))) return true;
    return m.results.some(r =>
      r.title.toLowerCase().includes(q) || (r.artist || "").toLowerCase().includes(q)
    );
  });
}

export function sortMatches(matches: readonly StoredMatch[], how: MatchSort): StoredMatch[] {
  const a = matches.slice();
  const score = (m: StoredMatch) => m.results[0]?.score ?? 0;
  switch (how) {
    case "ts_desc": a.sort((x, y) => y.ts - x.ts); break;
    case "ts_asc": a.sort((x, y) => x.ts - y.ts); break;
    case "score_desc": a.sort((x, y) => score(y) - score(x)); break;
    case "score_asc": a.sort((x, y) => score(x) - score(y)); break;
    case "latency_asc": a.sort((x, y) => x.elapsed_ms - y.elapsed_ms); break;
    case "latency_desc": a.sort((x, y) => y.elapsed_ms - x.elapsed_ms); break;
  }
  return a;
}

/** Aggregate fingerprinted tracks from recent matches (since no /tracks endpoint exists). */
export interface CatalogTrack {
  track_id: string;
  title: string;
  artist: string;
  album?: string;
  source: string;
  artwork_url?: string | null;
  preview_url?: string | null;
  tempo_bpm?: number | null;
  seen: number;
  best_score: number;
}

export function deriveCatalog(): CatalogTrack[] {
  const map = new Map<string, CatalogTrack>();
  for (const m of loadMatches()) {
    for (const r of m.results) {
      const prev = map.get(r.track_id);
      if (!prev) {
        map.set(r.track_id, {
          track_id: r.track_id,
          title: r.title,
          artist: r.artist,
          album: r.album,
          source: r.source,
          artwork_url: r.artwork_url ?? null,
          preview_url: r.preview_url ?? null,
          tempo_bpm: r.tempo_bpm ?? null,
          seen: 1,
          best_score: r.score,
        });
      } else {
        prev.seen += 1;
        if (r.score > prev.best_score) prev.best_score = r.score;
      }
    }
  }
  return Array.from(map.values()).sort((a, b) => b.seen - a.seen);
}

export function downsampleFloat(arr: Float32Array | number[], target = 4096): number[] {
  const a = arr instanceof Float32Array ? arr : Float32Array.from(arr);
  if (a.length <= target) return Array.from(a);
  const stride = a.length / target;
  const out: number[] = [];
  for (let i = 0; i < target; i++) out.push(a[Math.floor(i * stride)]);
  return out;
}
