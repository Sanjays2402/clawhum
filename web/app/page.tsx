"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import CaptureSurface from "@/components/CaptureSurface";
import Spectrogram from "@/components/Spectrogram";
import OnboardingTour from "@/components/OnboardingTour";
import { markOnboardingStep } from "@/components/OnboardingTour";
import { swrFetcher, type Stats, type MatchResponse, extractQueryPitch } from "@/lib/api";
import { downsampleFloat, saveMatch } from "@/lib/history";

export default function Home() {
  const router = useRouter();
  const { data: stats } = useSWR<Stats>("/api/stats", swrFetcher);
  const [loading, setLoading] = useState(false);
  const [topK, setTopK] = useState(10);
  const [threshold, setThreshold] = useState(0.2);
  const [last, setLast] = useState<MatchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const k = Number(localStorage.getItem("clawhum.topk") || "10");
    const t = Number(localStorage.getItem("clawhum.thresh") || "0.2");
    if (k) setTopK(k);
    if (!isNaN(t)) setThreshold(t);
  }, []);

  useEffect(() => {
    localStorage.setItem("clawhum.topk", String(topK));
    localStorage.setItem("clawhum.thresh", String(threshold));
  }, [topK, threshold]);

  async function onAudio(blob: Blob, viz: Float32Array, dur: number) {
    setLoading(true); setErr(null);
    try {
      const fd = new FormData();
      const name = (blob as any).name || "capture.webm";
      fd.append("audio", blob, name);
      fd.append("top_k", String(topK));
      fd.append("threshold", String(threshold));
      const r = await fetch("/api/match", { method: "POST", body: fd });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`${r.status} ${r.statusText} / ${t.slice(0, 160)}`);
      }
      const j = (await r.json()) as MatchResponse;
      setLast(j);
      // Fire-and-forget pitch extraction so the match detail page can render
      // a contour overlay even though the original blob isn't persisted.
      const pitch = await extractQueryPitch(blob, name);
      saveMatch({
        query_id: j.query_id,
        ts: Date.now(),
        elapsed_ms: j.elapsed_ms,
        count: j.count,
        filename: name,
        duration_sec: dur,
        query_waveform: downsampleFloat(viz, 4096),
        query_pitch: pitch ?? undefined,
        results: j.results,
      });
      // Onboarding progress: ran a match, results rendered, and we saved.
      markOnboardingStep("tried");
      markOnboardingStep("viewed");
      markOnboardingStep("saved");
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <OnboardingTour onRunSample={async (file) => {
        try {
          const resp = await fetch(file);
          if (!resp.ok) throw new Error(`sample fetch failed (${resp.status})`);
          const blob = await resp.blob();
          // Decode just enough to get a viz waveform + duration. Reuse the
          // same onAudio path so history + onboarding hooks fire identically
          // to a live capture.
          const arrBuf = await blob.arrayBuffer();
          const Ctor = (window.AudioContext || (window as any).webkitAudioContext) as typeof AudioContext;
          const ac = new Ctor();
          const decoded = await ac.decodeAudioData(arrBuf.slice(0));
          const ch = decoded.getChannelData(0);
          const viz = new Float32Array(ch.length);
          viz.set(ch);
          await ac.close();
          // Tag the blob with a filename so the API sees a real name.
          (blob as any).name = file.split("/").pop() || "sample.wav";
          await onAudio(blob, viz, decoded.duration);
        } catch (e: any) {
          setErr(e?.message || String(e));
        }
      }} />
      <section className="px-4 pt-4">
        <CaptureSurface onAudio={onAudio} loading={loading} topK={topK} threshold={threshold} />
      </section>

      {/* Param row */}
      <section className="px-4 mt-4">
        <div className="panel rounded-[2px] px-4 py-3 flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="label-xs">top_k</span>
            <input
              type="number" min={1} max={50} value={topK}
              onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              className="w-16 font-mono"
            />
            <input
              type="range" min={1} max={50} value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              className="w-40"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="label-xs">threshold</span>
            <input
              type="number" min={0} max={1} step={0.01} value={threshold}
              onChange={(e) => setThreshold(Math.max(0, Math.min(1, Number(e.target.value) || 0)))}
              className="w-20 font-mono"
            />
            <input
              type="range" min={0} max={1} step={0.01} value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-40"
            />
          </div>
          <div className="ml-auto flex items-center gap-6 font-mono text-[11px] text-[var(--color-muted)]">
            <span>index <span className="text-[var(--color-text)] tabular-nums">{stats?.vectors ?? "—"}</span> vec</span>
            <span>tracks <span className="text-[var(--color-text)] tabular-nums">{stats?.tracks ?? "—"}</span></span>
            <span>dim <span className="text-[var(--color-text)] tabular-nums">{stats?.dim ?? "—"}</span></span>
            <span>backend <span className="text-[var(--color-phosphor)]">{stats?.backend ?? "—"}</span></span>
          </div>
        </div>
      </section>

      {/* Error */}
      {err && (
        <section className="px-4 mt-4">
          <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] px-3 py-2 text-[var(--color-amber)] font-mono text-xs">
            match request failed / {err}
          </div>
        </section>
      )}

      {/* Results table + spectrograms */}
      <section className="px-4 mt-4 pb-12">
        <div className="flex items-end justify-between mb-2">
          <h2 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            candidates <span className="text-[var(--color-text)]">/ {last?.count ?? 0}</span>
          </h2>
          {last && (
            <div className="font-mono text-[11px] text-[var(--color-muted)] flex gap-4">
              <span>query_id <span className="text-[var(--color-text)]">{last.query_id.slice(0, 8)}</span></span>
              <span>elapsed <span className="text-[var(--color-phosphor)] tabular-nums">{last.elapsed_ms}</span> ms</span>
              <button
                onClick={() => router.push(`/matches/${last.query_id}`)}
                className="text-[var(--color-phosphor)] hover:underline"
              >open detail →</button>
            </div>
          )}
        </div>

        {!last && !loading && (
          <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-dim)]">
            no query yet / drop a file or arm the recorder
          </div>
        )}

        {loading && (
          <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-phosphor)]">
            <span className="animate-pulse">computing chroma + matching against index...</span>
          </div>
        )}

        {last && last.results.length > 0 && (
          <div className="panel rounded-[2px] overflow-hidden">
            <table className="dense-table">
              <thead>
                <tr>
                  <th className="w-10">#</th>
                  <th>track</th>
                  <th className="w-[200px]">artist</th>
                  <th className="w-[280px]">spectrogram</th>
                  <th className="w-[80px] text-right">seg</th>
                  <th className="w-[110px] text-right">score</th>
                  <th className="w-[80px] text-right">bpm</th>
                  <th className="w-[90px]">source</th>
                </tr>
              </thead>
              <tbody>
                {last.results.map((r, i) => (
                  <tr key={r.track_id + i} className="cursor-pointer" onClick={() => router.push(`/matches/${last.query_id}?cand=${i}`)}>
                    <td className="font-mono text-[var(--color-muted)] tabular-nums">{String(i + 1).padStart(2, "0")}</td>
                    <td>
                      <div className="text-[var(--color-text)]">{r.title || <span className="text-[var(--color-dim)]">untitled</span>}</div>
                      <div className="font-mono text-[10px] text-[var(--color-dim)] mt-0.5">{r.track_id}</div>
                    </td>
                    <td className="text-[var(--color-muted)]">{r.artist || <span className="text-[var(--color-dim)]">—</span>}</td>
                    <td>
                      <Spectrogram height={36} seed={r.track_id} />
                    </td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-muted)]">{r.segment_index}</td>
                    <td className="font-mono text-right tabular-nums">
                      <span className={r.score >= 0.5 ? "text-[var(--color-phosphor)]" : r.score >= 0.3 ? "text-[var(--color-amber)]" : "text-[var(--color-muted)]"}>
                        {r.score.toFixed(4)}
                      </span>
                    </td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-muted)]">{r.tempo_bpm ? r.tempo_bpm.toFixed(0) : "—"}</td>
                    <td className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">{r.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {last && last.results.length === 0 && (
          <div className="panel-inset rounded-[2px] py-12 text-center font-mono text-xs text-[var(--color-amber)]">
            no candidates above threshold / try lowering threshold or recording a longer fingerprint window
          </div>
        )}
      </section>
    </div>
  );
}
