/**
 * Shared types and adapters for the POST /api/share endpoint.
 *
 * The share endpoint accepts the same compact payload whether you are
 * sharing a fresh capture (StoredMatch) or a row from the cloud history
 * (HistoryItem). This module exposes a single canonical input shape and
 * tiny adapters so both call sites stay in lockstep with the server
 * schema.
 */

import type { StoredMatch } from "./history";
import type { MatchResult } from "./api";

/**
 * Match result fields that the share endpoint actually persists.
 * Mirrors services/api/clawhum_api/schemas.MatchResult.
 */
export interface ShareResult {
  track_id: string;
  title: string;
  artist?: string;
  album?: string;
  score: number;
  segment_index?: number;
  preview_url?: string | null;
  artwork_url?: string | null;
  source?: string;
  tempo_bpm?: number | null;
}

/** Wire-format body for POST /api/share. */
export interface ShareInput {
  query_id: string;
  elapsed_ms: number;
  count: number;
  results: ShareResult[];
  filename: string | null;
  duration_sec: number | null;
  /** Optional human note attached to the share. */
  note?: string | null;
}

/**
 * Minimal subset of HistoryItem we need. Kept structural so the history
 * page does not have to import this file for typing reasons.
 */
export interface HistoryLike {
  query_id: string;
  elapsed_ms: number;
  count: number;
  results: ShareResult[];
  filename: string | null;
  duration_sec: number | null;
}

/** Build a share payload from a client-side StoredMatch. */
export function toShareInput(m: StoredMatch): ShareInput {
  return {
    query_id: m.query_id,
    elapsed_ms: m.elapsed_ms,
    count: m.count,
    results: m.results.map(normaliseResult),
    filename: m.filename ?? null,
    duration_sec: m.duration_sec ?? null,
  };
}

/** Build a share payload from a server-side history row. */
export function historyToShareInput(
  it: HistoryLike,
  note?: string | null,
): ShareInput {
  return {
    query_id: it.query_id,
    elapsed_ms: it.elapsed_ms,
    count: it.count,
    results: (it.results || []).map(normaliseResult),
    filename: it.filename ?? null,
    duration_sec: it.duration_sec ?? null,
    note: note ?? null,
  };
}

/**
 * Coerce a result row into the wire shape, defaulting any optional
 * fields the server tolerates as missing. Defensive against partial
 * JSON returned by older clients or seeded fixtures.
 */
export function normaliseResult(r: Partial<MatchResult> | ShareResult): ShareResult {
  return {
    track_id: String((r as ShareResult).track_id ?? ""),
    title: String((r as ShareResult).title ?? ""),
    artist: (r as ShareResult).artist ?? undefined,
    album: (r as ShareResult).album ?? undefined,
    score: Number((r as ShareResult).score ?? 0),
    segment_index:
      typeof r.segment_index === "number" ? r.segment_index : undefined,
    preview_url: r.preview_url ?? null,
    artwork_url: r.artwork_url ?? null,
    source: r.source ?? undefined,
    tempo_bpm: r.tempo_bpm ?? null,
  };
}

/** True when an input is well-formed enough to POST. */
export function isShareable(input: ShareInput | null | undefined): input is ShareInput {
  if (!input) return false;
  if (!input.query_id) return false;
  if (!Array.isArray(input.results) || input.results.length === 0) return false;
  if (input.results.length > 50) return false;
  return true;
}
