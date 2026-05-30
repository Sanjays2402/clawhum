export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

export interface MatchResult {
  track_id: string;
  title: string;
  artist: string;
  album?: string;
  score: number;
  segment_index: number;
  preview_url?: string | null;
  artwork_url?: string | null;
  source: string;
  tempo_bpm?: number | null;
}

export interface MatchResponse {
  query_id: string;
  elapsed_ms: number;
  count: number;
  results: MatchResult[];
}

export interface Stats {
  tracks: number;
  vectors: number;
  dim: number;
  backend: string;
}

export interface Health {
  ok: boolean;
  version: string;
  embedder: string;
  index_backend: string;
  tracks: number;
  vectors: number;
}

export async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, init);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const swrFetcher = <T = unknown>(path: string): Promise<T> => jsonFetch<T>(path);

export async function textFetch(path: string): Promise<string> {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.text();
}
