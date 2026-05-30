"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Waveform from "@/components/Waveform";
import Spectrogram from "@/components/Spectrogram";
import { getMatch, type StoredMatch } from "@/lib/history";

function ScoreBar({ value, max, active }: { value: number; max: number; active?: boolean }) {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <div className="meter-track h-2 rounded-[1px] flex-1">
      <div
        className="meter-fill"
        style={{
          width: `${pct}%`,
          background: active
            ? "linear-gradient(90deg, var(--color-magenta), var(--color-phosphor))"
            : undefined,
        }}
      />
    </div>
  );
}

export default function MatchDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const search = useSearchParams();
  const candParam = Number(search.get("cand") || 0);
  const [m, setM] = useState<StoredMatch | null | "missing">(null);
  const [active, setActive] = useState(candParam);
  const [vote, setVote] = useState<Record<string, number>>({});

  useEffect(() => {
    const found = getMatch(id);
    setM(found || "missing");
  }, [id]);

  useEffect(() => { setActive(candParam); }, [candParam]);

  if (m === null) {
    return <div className="px-4 py-8 font-mono text-xs text-[var(--color-muted)]">loading...</div>;
  }
  if (m === "missing") {
    return (
      <div className="px-4 py-8">
        <div className="panel-inset rounded-[2px] py-12 text-center font-mono text-xs text-[var(--color-amber)]">
          query_id <span className="text-[var(--color-text)]">{id.slice(0, 12)}</span> not in local log
          <div className="mt-3">
            <Link href="/matches" className="text-[var(--color-phosphor)] hover:underline">← back to query log</Link>
          </div>
        </div>
      </div>
    );
  }

  const cand = m.results[active] ?? m.results[0];
  const dur = m.duration_sec || 0;
  const maxScore = m.results.length ? Math.max(...m.results.map(r => r.score)) : 1;

  // Synthesize "matched segment" highlight in the query waveform aligned to the candidate's segment_index.
  // Match window is conventionally ~1s slid by hop; we render a 1s band centered on segment_index seconds.
  const segCenter = (cand?.segment_index ?? 0) + 0.5;
  const highlight: [number, number] | null = dur > 0
    ? [Math.max(0, segCenter - 0.5), Math.min(dur, segCenter + 0.5)]
    : null;

  async function sendVote(v: number) {
    if (!cand || m === null || m === "missing") return;
    setVote(prev => ({ ...prev, [cand.track_id]: v }));
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query_id: m.query_id, track_id: cand.track_id, score: cand.score, vote: v }),
      });
    } catch {}
  }

  const queryWf = m.query_waveform ? Float32Array.from(m.query_waveform) : null;

  return (
    <div className="px-4 py-4 space-y-4">
      {/* Header strip */}
      <div className="panel rounded-[2px] px-4 py-3 flex flex-wrap items-center gap-6">
        <Link href="/matches" className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
          ← log
        </Link>
        <div className="font-mono text-[11px]">
          <span className="label-xs mr-2">query_id</span>
          <span className="text-[var(--color-phosphor)]">{m.query_id}</span>
        </div>
        <div className="font-mono text-[11px] text-[var(--color-muted)]">
          <span className="label-xs mr-2">latency</span>
          <span className="text-[var(--color-phosphor)] tabular-nums">{m.elapsed_ms}</span>
          <span className="text-[var(--color-dim)] ml-0.5">ms</span>
        </div>
        <div className="font-mono text-[11px] text-[var(--color-muted)]">
          <span className="label-xs mr-2">candidates</span>
          <span className="text-[var(--color-text)] tabular-nums">{m.count}</span>
        </div>
        <div className="font-mono text-[11px] text-[var(--color-muted)]">
          <span className="label-xs mr-2">window</span>
          <span className="text-[var(--color-text)] tabular-nums">{dur.toFixed(2)}</span>
          <span className="text-[var(--color-dim)] ml-0.5">s</span>
        </div>
      </div>

      {/* Twin waveforms */}
      <div className="panel rounded-[2px] overflow-hidden">
        <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between">
          <span className="label-xs">query / captured fingerprint</span>
          <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">{m.filename || "stream"}</span>
        </div>
        <div className="px-2 py-2">
          <Waveform
            samples={queryWf}
            height={160}
            animate={false}
            color="#3DFC8E"
            duration={dur || undefined}
            highlight={highlight}
            label={highlight ? `match band ${highlight[0].toFixed(2)}s → ${highlight[1].toFixed(2)}s` : undefined}
          />
        </div>
        <div className="px-3 py-2 border-y border-[var(--color-line)] flex items-center justify-between bg-[var(--color-panel-2)]">
          <span className="label-xs text-[var(--color-magenta)]">matched segment / {cand?.title || "—"}</span>
          <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
            seg <span className="text-[var(--color-text)]">{cand?.segment_index ?? 0}</span>
          </span>
        </div>
        <div className="px-2 py-2">
          <Waveform
            samples={null}
            height={160}
            animate
            color="#FF3DBE"
            duration={dur || undefined}
            highlight={highlight}
            label="reference segment (placeholder)"
          />
        </div>
        <div className="px-3 py-2 border-t border-[var(--color-line)]">
          <Spectrogram height={56} showAxis seed={cand?.track_id || m.query_id} label="reference chroma / mfcc bins" />
        </div>
      </div>

      {/* Candidates with score bars */}
      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4">
        <div className="panel rounded-[2px] overflow-hidden">
          <div className="px-3 py-2 border-b border-[var(--color-line)] flex justify-between">
            <span className="label-xs">candidate scores</span>
            <span className="font-mono text-[10px] text-[var(--color-dim)]">click to select</span>
          </div>
          <div className="divide-y divide-[var(--color-line)]">
            {m.results.length === 0 && (
              <div className="py-10 text-center font-mono text-xs text-[var(--color-dim)]">no candidates returned</div>
            )}
            {m.results.map((r, i) => (
              <button
                key={r.track_id + i}
                onClick={() => setActive(i)}
                className={`w-full text-left px-3 py-2 flex items-center gap-3 hover:bg-[rgba(61,252,142,0.04)] ${i === active ? "bg-[rgba(255,61,190,0.06)]" : ""}`}
              >
                <span className="font-mono text-[10px] w-6 text-right tabular-nums text-[var(--color-muted)]">{String(i + 1).padStart(2, "0")}</span>
                <div className="w-44 min-w-0">
                  <div className="text-[13px] truncate">{r.title || <span className="text-[var(--color-dim)]">untitled</span>}</div>
                  <div className="font-mono text-[10px] text-[var(--color-dim)] truncate">{r.artist || "—"}</div>
                </div>
                <ScoreBar value={r.score} max={maxScore} active={i === active} />
                <span className={`font-mono text-[12px] tabular-nums w-16 text-right ${r.score >= 0.5 ? "text-[var(--color-phosphor)]" : r.score >= 0.3 ? "text-[var(--color-amber)]" : "text-[var(--color-muted)]"}`}>
                  {r.score.toFixed(4)}
                </span>
                <Spectrogram height={28} width={120} seed={r.track_id} />
              </button>
            ))}
          </div>
        </div>

        {/* Selected metadata */}
        <div className="panel rounded-[2px] p-4 space-y-3">
          <div className="label-xs">selected candidate</div>
          {cand ? (
            <>
              <div className="text-lg font-medium">{cand.title || <span className="text-[var(--color-dim)]">untitled</span>}</div>
              <div className="text-[var(--color-muted)]">{cand.artist || "—"}{cand.album ? ` / ${cand.album}` : ""}</div>
              <div className="grid grid-cols-2 gap-2 pt-2 font-mono text-[11px]">
                <Field k="track_id" v={cand.track_id} />
                <Field k="score" v={cand.score.toFixed(6)} accent />
                <Field k="segment" v={String(cand.segment_index)} />
                <Field k="source" v={cand.source} />
                <Field k="tempo" v={cand.tempo_bpm ? `${cand.tempo_bpm.toFixed(1)} bpm` : "—"} />
              </div>
              {cand.preview_url && (
                <div>
                  <div className="label-xs mb-1">preview</div>
                  <audio controls src={cand.preview_url} className="w-full h-8" />
                </div>
              )}
              <div className="pt-2 border-t border-[var(--color-line)]">
                <div className="label-xs mb-2">feedback</div>
                <div className="flex gap-2">
                  <button
                    onClick={() => sendVote(1)}
                    className={`flex-1 px-3 py-2 rounded-[2px] font-mono text-[11px] uppercase tracking-widest border transition
                      ${vote[cand.track_id] === 1 ? "border-[var(--color-phosphor)] bg-[rgba(61,252,142,0.1)] text-[var(--color-phosphor)]" : "border-[var(--color-line-2)] text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)]"}`}
                  >
                    confirm match
                  </button>
                  <button
                    onClick={() => sendVote(-1)}
                    className={`flex-1 px-3 py-2 rounded-[2px] font-mono text-[11px] uppercase tracking-widest border transition
                      ${vote[cand.track_id] === -1 ? "border-[var(--color-red)] bg-[rgba(255,77,94,0.1)] text-[var(--color-red)]" : "border-[var(--color-line-2)] text-[var(--color-muted)] hover:text-[var(--color-red)] hover:border-[var(--color-red)]"}`}
                  >
                    reject
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="font-mono text-xs text-[var(--color-dim)]">no candidate selected</div>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">{k}</div>
      <div className={`mt-0.5 tabular-nums truncate ${accent ? "text-[var(--color-phosphor)]" : "text-[var(--color-text)]"}`}>{v}</div>
    </div>
  );
}
