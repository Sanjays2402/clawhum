"use client";
import { useState } from "react";

export interface MatchResult {
  track_id: string; title: string; artist: string; album?: string;
  score: number; segment_index: number; preview_url?: string | null;
  artwork_url?: string | null; source: string;
}

export default function ResultsList({ results, queryId }: { results: MatchResult[]; queryId: string | null }) {
  if (!results.length) return null;
  return (
    <ul className="space-y-3">
      {results.map((r, i) => <Row key={r.track_id} r={r} i={i} queryId={queryId} />)}
    </ul>
  );
}

function Row({ r, i, queryId }: { r: MatchResult; i: number; queryId: string | null }) {
  const [vote, setVote] = useState<number | null>(null);
  async function sendVote(v: number) {
    setVote(v);
    if (!queryId) return;
    await fetch("/api/feedback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: queryId, track_id: r.track_id, score: r.score, vote: v }),
    });
  }
  return (
    <li className="flex items-center gap-4 p-4 bg-[var(--panel)] border border-[var(--line)] rounded-xl">
      <div className="w-10 text-right text-[var(--muted)]">{i + 1}</div>
      {r.artwork_url ? (
        <img src={r.artwork_url} alt="" className="w-12 h-12 rounded" />
      ) : <div className="w-12 h-12 rounded bg-black/40" />}
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate">{r.title}</div>
        <div className="text-sm text-[var(--muted)] truncate">{r.artist}</div>
      </div>
      <div className="text-sm tabular-nums text-[var(--accent)]">{r.score.toFixed(3)}</div>
      {r.preview_url && (
        <audio controls src={r.preview_url} className="h-8" />
      )}
      <div className="flex gap-1">
        <button onClick={() => sendVote(1)}
                className={`px-2 py-1 rounded text-sm ${vote === 1 ? "bg-[var(--accent)] text-black" : "bg-black/40"}`}>+</button>
        <button onClick={() => sendVote(-1)}
                className={`px-2 py-1 rounded text-sm ${vote === -1 ? "bg-red-500 text-white" : "bg-black/40"}`}>-</button>
      </div>
    </li>
  );
}
