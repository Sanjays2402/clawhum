// Client-side persisted match history (the API doesn't store queries).
import type { MatchResponse, MatchResult } from "./api";

export interface StoredMatch {
  query_id: string;
  ts: number;             // ms epoch
  elapsed_ms: number;
  count: number;
  filename?: string;
  duration_sec?: number;
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
