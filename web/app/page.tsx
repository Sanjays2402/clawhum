"use client";
import { useState } from "react";
import HumRecorder from "@/components/HumRecorder";
import ResultsList, { MatchResult } from "@/components/ResultsList";

export default function Home() {
  const [results, setResults] = useState<MatchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [queryId, setQueryId] = useState<string | null>(null);
  const [topK, setTopK] = useState(10);
  const [threshold, setThreshold] = useState(0.2);

  async function onAudio(blob: Blob) {
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append("audio", blob, "hum.webm");
      fd.append("top_k", String(topK));
      fd.append("threshold", String(threshold));
      const r = await fetch("/api/match", { method: "POST", body: fd });
      const j = await r.json();
      setResults(j.results || []);
      setElapsed(j.elapsed_ms ?? null);
      setQueryId(j.query_id ?? null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="gradient-hero">
      <section className="px-6 pt-20 pb-24 max-w-4xl mx-auto text-center">
        <h1 className="text-5xl md:text-6xl font-bold tracking-tight">
          Hum it. <span className="text-[var(--accent)]">Find it.</span>
        </h1>
        <p className="mt-4 text-[var(--muted)] text-lg">
          Stuck on a tune? Hum a few seconds. We will match it against your library.
        </p>
        <div className="mt-10">
          <HumRecorder onAudio={onAudio} loading={loading} />
        </div>
        <div className="mt-8 flex justify-center gap-6 text-sm text-[var(--muted)]">
          <label className="flex items-center gap-2">
            top_k
            <input type="number" min={1} max={50} value={topK}
                   onChange={e => setTopK(Number(e.target.value))}
                   className="w-16 bg-[var(--panel)] border border-[var(--line)] rounded px-2 py-1 text-white" />
          </label>
          <label className="flex items-center gap-2">
            threshold
            <input type="number" min={0} max={1} step={0.05} value={threshold}
                   onChange={e => setThreshold(Number(e.target.value))}
                   className="w-20 bg-[var(--panel)] border border-[var(--line)] rounded px-2 py-1 text-white" />
          </label>
        </div>
        {elapsed !== null && (
          <p className="mt-4 text-xs text-[var(--muted)]">matched in {elapsed} ms</p>
        )}
      </section>

      <section className="px-6 pb-24 max-w-3xl mx-auto">
        <ResultsList results={results} queryId={queryId} />
      </section>
    </div>
  );
}
