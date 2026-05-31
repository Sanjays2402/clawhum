"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { MusicNotes, Play, Pause, MagnifyingGlass, Lightning, Waveform as WaveformIcon } from "@phosphor-icons/react/dist/ssr";
import Waveform from "@/components/Waveform";
import { swrFetcher, type MatchResponse, type MatchResult } from "@/lib/api";
import useSWR from "swr";
import { saveMatch, downsampleFloat } from "@/lib/history";

interface Sample {
  id: string;
  title: string;
  composer: string;
  key: string;
  bpm: number;
  duration_sec: number;
  file: string;
}

interface Manifest {
  version: number;
  note: string;
  samples: Sample[];
}

interface RunState {
  loading: boolean;
  error: string | null;
  result: MatchResponse | null;
  waveform: Float32Array | null;
}

const EMPTY: RunState = { loading: false, error: null, result: null, waveform: null };

async function decodeWav(url: string): Promise<{ blob: Blob; waveform: Float32Array; duration: number }> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch ${url}: ${r.status}`);
  const buf = await r.arrayBuffer();
  const blob = new Blob([buf], { type: "audio/wav" });
  const Ctor: typeof AudioContext = (window as any).AudioContext || (window as any).webkitAudioContext;
  const ac = new Ctor();
  try {
    const decoded = await ac.decodeAudioData(buf.slice(0));
    const ch0 = decoded.getChannelData(0);
    const wf = new Float32Array(ch0.length);
    wf.set(ch0);
    return { blob, waveform: wf, duration: decoded.duration };
  } finally {
    ac.close();
  }
}

export default function DemoPage() {
  const { data: manifest, error: manifestErr } = useSWR<Manifest>("/samples/manifest.json", swrFetcher);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [playing, setPlaying] = useState<string | null>(null);
  const [audioEl] = useState<HTMLAudioElement | null>(() =>
    typeof window === "undefined" ? null : new Audio(),
  );

  useEffect(() => {
    if (!audioEl) return;
    const onEnd = () => setPlaying(null);
    audioEl.addEventListener("ended", onEnd);
    return () => audioEl.removeEventListener("ended", onEnd);
  }, [audioEl]);

  function setRun(id: string, patch: Partial<RunState>) {
    setRuns((prev) => ({ ...prev, [id]: { ...EMPTY, ...prev[id], ...patch } }));
  }

  function togglePlay(s: Sample) {
    if (!audioEl) return;
    if (playing === s.id) {
      audioEl.pause();
      setPlaying(null);
      return;
    }
    audioEl.pause();
    audioEl.src = s.file;
    audioEl.currentTime = 0;
    audioEl.play().then(() => setPlaying(s.id)).catch(() => setPlaying(null));
  }

  async function runMatch(s: Sample) {
    setRun(s.id, { loading: true, error: null, result: null });
    try {
      const { blob, waveform, duration } = await decodeWav(s.file);
      let wf = waveform;
      if (!runs[s.id]?.waveform) setRun(s.id, { waveform: wf, loading: true });
      const fd = new FormData();
      fd.append("audio", blob, `${s.id}.wav`);
      fd.append("top_k", "10");
      fd.append("threshold", "0.0");
      const r = await fetch("/api/match", { method: "POST", body: fd });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`${r.status} ${r.statusText} ${t.slice(0, 200)}`);
      }
      const j = (await r.json()) as MatchResponse;
      setRun(s.id, { loading: false, result: j, waveform: wf });
      try {
        saveMatch({
          query_id: j.query_id,
          ts: Date.now(),
          elapsed_ms: j.elapsed_ms,
          count: j.count,
          filename: `${s.id}.wav`,
          duration_sec: duration,
          query_waveform: downsampleFloat(wf, 4096),
          results: j.results,
        });
      } catch {}
    } catch (e: any) {
      setRun(s.id, { loading: false, error: e?.message || String(e) });
    }
  }

  return (
    <div className="px-4 pt-4 pb-12 max-w-6xl mx-auto">
      <header className="panel rounded-[2px] px-4 py-4 mb-4">
        <div className="flex items-start gap-3">
          <MusicNotes size={28} weight="duotone" className="text-[var(--color-phosphor)] mt-0.5" />
          <div className="flex-1">
            <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-phosphor)]">
              demo / try a sample
            </h1>
            <p className="text-[12px] text-[var(--color-muted)] mt-1 max-w-2xl leading-relaxed">
              Three public-domain melodies, rendered as short hum-like clips. Click a card to preview the
              audio, then hit Match to run it through the same /match endpoint a recorded hum would hit.
              Results are real predictions from the index, with latency and confidence.
            </p>
          </div>
          <Link
            href="/"
            className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] border border-[var(--color-line)] px-3 py-1.5"
          >
            record your own
          </Link>
        </div>
      </header>

      {manifestErr && (
        <div className="panel rounded-[2px] px-4 py-3 mb-4 text-[12px] text-[var(--color-error,#ff5577)] font-mono">
          failed to load samples manifest
        </div>
      )}

      {!manifest && !manifestErr && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="panel rounded-[2px] h-[260px] animate-pulse" />
          ))}
        </div>
      )}

      {manifest && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {manifest.samples.map((s) => {
            const run = runs[s.id] ?? EMPTY;
            const isPlaying = playing === s.id;
            return (
              <article key={s.id} className="panel rounded-[2px] p-4 flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="font-mono text-[12px] uppercase tracking-widest text-[var(--color-text)] truncate">
                      {s.title}
                    </h2>
                    <p className="text-[11px] text-[var(--color-dim)] mt-0.5 truncate">{s.composer}</p>
                  </div>
                  <span className="font-mono text-[9px] uppercase tracking-widest text-[var(--color-dim)] border border-[var(--color-line)] px-1.5 py-0.5">
                    {s.key}
                  </span>
                </div>

                <div className="border border-[var(--color-line)]">
                  <Waveform
                    samples={run.waveform || null}
                    height={72}
                    duration={s.duration_sec}
                    ticks={false}
                    animate={!run.waveform}
                    label={run.waveform ? "decoded" : "preview"}
                    compact
                  />
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => togglePlay(s)}
                    className="flex items-center gap-1.5 border border-[var(--color-line)] px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--color-text)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)]"
                    aria-label={isPlaying ? "Pause sample" : "Play sample"}
                  >
                    {isPlaying ? <Pause size={14} weight="duotone" /> : <Play size={14} weight="duotone" />}
                    {isPlaying ? "pause" : "play"}
                  </button>
                  <button
                    onClick={() => runMatch(s)}
                    disabled={run.loading}
                    className="flex items-center gap-1.5 bg-[var(--color-phosphor)] text-black px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-widest disabled:opacity-40"
                    aria-label={`Match ${s.title}`}
                  >
                    <MagnifyingGlass size={14} weight="duotone" />
                    {run.loading ? "matching..." : "match"}
                  </button>
                  <span className="ml-auto font-mono text-[9px] text-[var(--color-dim)] uppercase tracking-widest">
                    {s.bpm} bpm
                  </span>
                </div>

                {run.error && (
                  <div className="font-mono text-[10px] text-[#ff5577] break-words border border-[#ff557755] px-2 py-1.5">
                    {run.error}
                  </div>
                )}

                {run.result && <ResultBlock res={run.result} />}

                {!run.result && !run.loading && !run.error && (
                  <div className="text-[10px] text-[var(--color-dim)] font-mono uppercase tracking-widest">
                    awaiting match
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      <footer className="mt-6 panel rounded-[2px] px-4 py-3">
        <div className="flex flex-wrap items-center gap-4 text-[11px] text-[var(--color-muted)] font-mono">
          <span className="flex items-center gap-1.5">
            <WaveformIcon size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            16 kHz mono WAV
          </span>
          <span>POST /match (multipart/form-data)</span>
          <span>top_k 10 / threshold 0.0</span>
          <Link href="/matches" className="ml-auto hover:text-[var(--color-phosphor)] uppercase tracking-widest">
            view full history
          </Link>
        </div>
      </footer>
    </div>
  );
}

function ResultBlock({ res }: { res: MatchResponse }) {
  const top = res.results[0];
  const max = useMemo(
    () => Math.max(0.0001, ...res.results.map((r) => r.score)),
    [res],
  );
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
        <span className="flex items-center gap-1">
          <Lightning size={12} weight="duotone" className="text-[var(--color-phosphor)]" />
          {res.elapsed_ms.toFixed(0)} ms / {res.count} hits
        </span>
        <span className="truncate">{res.query_id.slice(0, 8)}</span>
      </div>

      {res.results.length === 0 && (
        <div className="text-[11px] text-[var(--color-muted)] font-mono">
          no matches above threshold. index may be empty. run reindex from /library.
        </div>
      )}

      {top && (
        <div className="border border-[var(--color-phosphor)] bg-[var(--color-phosphor)]/5 px-2.5 py-1.5">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="font-mono text-[11px] text-[var(--color-phosphor)] truncate">
                {top.title}
              </div>
              <div className="font-mono text-[10px] text-[var(--color-muted)] truncate">
                {top.artist}
              </div>
            </div>
            <div className="font-mono text-[12px] text-[var(--color-phosphor)] tabular-nums">
              {top.score.toFixed(3)}
            </div>
          </div>
        </div>
      )}

      {res.results.length > 1 && (
        <div className="flex flex-col gap-0.5 mt-1">
          {res.results.slice(1, 6).map((r) => (
            <Bar key={`${r.track_id}-${r.segment_index}`} r={r} max={max} />
          ))}
        </div>
      )}
    </div>
  );
}

function Bar({ r, max }: { r: MatchResult; max: number }) {
  const pct = Math.max(2, Math.round((r.score / max) * 100));
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 min-w-0">
        <div className="font-mono text-[10px] text-[var(--color-text)] truncate">{r.title}</div>
        <div className="relative h-1.5 bg-[var(--color-line)] overflow-hidden">
          <div
            className="absolute inset-y-0 left-0 bg-[var(--color-phosphor)]/60"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <span className="font-mono text-[10px] text-[var(--color-dim)] tabular-nums w-12 text-right">
        {r.score.toFixed(3)}
      </span>
    </div>
  );
}
