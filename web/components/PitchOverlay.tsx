"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import { Waveform as WaveformIcon } from "@phosphor-icons/react/dist/ssr";

export interface PitchContour {
  sr: number;
  duration_sec: number;
  hop_sec: number;
  times: number[];
  hz: (number | null)[];
  midi: (number | null)[];
  voiced_ratio: number;
  median_hz: number;
}

interface Props {
  /** Pre-computed query contour (saved at match time so detail page works offline). */
  queryContour?: PitchContour | null;
  /** Optional fallback: post this blob to /api/pitch if no contour is provided. */
  queryBlob?: Blob | null;
  trackId?: string;
  segmentIndex?: number;
  windowSec?: number;
}

type Fetched<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

function noteName(midi: number): string {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const n = Math.round(midi);
  const octave = Math.floor(n / 12) - 1;
  return `${names[((n % 12) + 12) % 12]}${octave}`;
}

function normalizeToCommonKey(midi: (number | null)[]): number {
  // Median midi (ignoring null) → use as detune offset target.
  const v: number[] = [];
  for (const m of midi) if (m != null && Number.isFinite(m)) v.push(m);
  if (!v.length) return 0;
  v.sort((a, b) => a - b);
  return v[Math.floor(v.length / 2)];
}

export default function PitchOverlay({
  queryContour,
  queryBlob,
  trackId,
  segmentIndex = 0,
  windowSec = 1,
}: Props) {
  const [q, setQ] = useState<Fetched<PitchContour>>({ status: "idle" });
  const [r, setR] = useState<Fetched<PitchContour>>({ status: "idle" });

  // Query contour
  useEffect(() => {
    if (queryContour) { setQ({ status: "ready", data: queryContour }); return; }
    if (!queryBlob) { setQ({ status: "idle" }); return; }
    let cancelled = false;
    setQ({ status: "loading" });
    (async () => {
      try {
        const fd = new FormData();
        fd.append("audio", queryBlob, "query.wav");
        const res = await fetch("/api/pitch", { method: "POST", body: fd });
        if (!res.ok) {
          const t = await res.text().catch(() => "");
          throw new Error(`${res.status} ${res.statusText} ${t.slice(0, 140)}`);
        }
        const data = (await res.json()) as PitchContour;
        if (!cancelled) setQ({ status: "ready", data });
      } catch (e: unknown) {
        const m = e instanceof Error ? e.message : String(e);
        if (!cancelled) setQ({ status: "error", message: m });
      }
    })();
    return () => { cancelled = true; };
  }, [queryBlob, queryContour]);

  // Reference contour
  useEffect(() => {
    if (!trackId) { setR({ status: "idle" }); return; }
    let cancelled = false;
    setR({ status: "loading" });
    (async () => {
      try {
        const url = `/api/track/${encodeURIComponent(trackId)}/pitch?segment_index=${segmentIndex}&window=${windowSec}`;
        const res = await fetch(url);
        if (res.status === 404) throw new Error("reference audio not available");
        if (!res.ok) {
          const t = await res.text().catch(() => "");
          throw new Error(`${res.status} ${res.statusText} ${t.slice(0, 140)}`);
        }
        const data = (await res.json()) as PitchContour;
        if (!cancelled) setR({ status: "ready", data });
      } catch (e: unknown) {
        const m = e instanceof Error ? e.message : String(e);
        if (!cancelled) setR({ status: "error", message: m });
      }
    })();
    return () => { cancelled = true; };
  }, [trackId, segmentIndex, windowSec]);

  const rows = useMemo(() => {
    const qData = q.status === "ready" ? q.data : null;
    const rData = r.status === "ready" ? r.data : null;
    if (!qData && !rData) return [];

    // Build a unified time axis (0 .. max duration) with shape-aware lookup.
    const qDur = qData?.duration_sec ?? 0;
    const rDur = rData?.duration_sec ?? 0;
    const dur = Math.max(qDur, rDur, 0.01);
    const n = 200;

    // Detune: shift both contours so their median sits at the same midi.
    // This makes "same melody, different octave / key" visually clear.
    const qMed = qData ? normalizeToCommonKey(qData.midi) : 0;
    const rMed = rData ? normalizeToCommonKey(rData.midi) : 0;
    const target = Math.round((qMed + rMed) / 2) || qMed || rMed || 60;

    const sample = (c: PitchContour | null, t: number, shift: number) => {
      if (!c || !c.midi.length) return null;
      const idx = Math.min(c.midi.length - 1, Math.max(0, Math.round((t / c.duration_sec) * (c.midi.length - 1))));
      const m = c.midi[idx];
      if (m == null) return null;
      return m + shift;
    };

    const out: { t: number; query: number | null; reference: number | null }[] = [];
    for (let i = 0; i < n; i++) {
      const t = (i / (n - 1)) * dur;
      out.push({
        t: Number(t.toFixed(3)),
        query: sample(qData, t, target - qMed),
        reference: sample(rData, t, target - rMed),
      });
    }
    return out;
  }, [q, r]);

  const stats = useMemo(() => {
    let agree = 0, both = 0;
    for (const row of rows) {
      if (row.query != null && row.reference != null) {
        both++;
        if (Math.abs(row.query - row.reference) <= 1.5) agree++; // within ~1.5 semitones
      }
    }
    const pct = both ? Math.round((agree / both) * 100) : 0;
    return { both, agree, pct };
  }, [rows]);

  const empty = q.status === "idle" && r.status === "idle";
  const loading = q.status === "loading" || r.status === "loading";
  const error = q.status === "error" ? q.message : r.status === "error" ? r.message : null;

  return (
    <div className="panel rounded-[2px] overflow-hidden">
      <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between">
        <span className="label-xs flex items-center gap-2">
          <WaveformIcon size={12} weight="duotone" />
          pitch contour overlay
        </span>
        <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
          {stats.both > 0
            ? <>agreement <span className="text-[var(--color-text)]">{stats.pct}%</span> across {stats.both} frames</>
            : <>melody shape (semitone, key normalised)</>}
        </span>
      </div>
      <div className="px-2 py-2">
        {empty ? (
          <div className="py-10 text-center font-mono text-[10px] text-[var(--color-dim)]">
            waiting for query and reference audio...
          </div>
        ) : error ? (
          <div className="py-10 text-center font-mono text-[10px] text-[var(--color-amber)]">
            pitch unavailable / {error}
          </div>
        ) : loading && rows.length === 0 ? (
          <div className="py-10 text-center font-mono text-[10px] text-[var(--color-dim)]">
            extracting pitch (pyin)...
          </div>
        ) : (
          <div style={{ width: "100%", height: 200 }}>
            <ResponsiveContainer>
              <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                <XAxis
                  dataKey="t"
                  tick={{ fill: "#666", fontSize: 10, fontFamily: "ui-monospace" }}
                  tickFormatter={(v: number) => `${v.toFixed(2)}s`}
                  stroke="#333"
                />
                <YAxis
                  domain={["auto", "auto"]}
                  tick={{ fill: "#666", fontSize: 10, fontFamily: "ui-monospace" }}
                  tickFormatter={(v: number) => noteName(v)}
                  width={42}
                  stroke="#333"
                />
                <Tooltip
                  contentStyle={{
                    background: "#0a0a0a", border: "1px solid #222",
                    fontFamily: "ui-monospace", fontSize: 11, borderRadius: 2,
                  }}
                  labelFormatter={((v: unknown) => `t = ${Number(v).toFixed(3)}s`) as never}
                  formatter={((value: unknown, name: unknown) => {
                    const n = typeof value === "number" ? value : Number(value);
                    if (!Number.isFinite(n)) return ["—", String(name)];
                    return [`${noteName(n)} (${n.toFixed(2)})`, String(name)];
                  }) as never}
                />
                <Legend
                  iconType="plainline"
                  wrapperStyle={{ fontFamily: "ui-monospace", fontSize: 10, color: "#888" }}
                />
                <Line
                  type="monotone" dataKey="query" name="query" stroke="#3DFC8E"
                  strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive={false}
                />
                <Line
                  type="monotone" dataKey="reference" name="reference" stroke="#FF3DBE"
                  strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
