"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  AreaChart,
  Area,
  Cell,
} from "recharts";
import {
  ChartBar,
  Lightning,
  MusicNotes,
  Target,
  Waveform as WaveformIcon,
  Clock,
} from "@phosphor-icons/react/dist/ssr";
import { loadMatches, type StoredMatch } from "@/lib/history";

interface Bucket {
  label: string;
  count: number;
  color?: string;
}

interface TopTrack {
  track_id: string;
  title: string;
  artist: string;
  seen: number;
  best_score: number;
  mean_score: number;
  source: string;
}

interface DailyPoint {
  day: string;       // YYYY-MM-DD
  ts: number;        // start-of-day epoch ms
  queries: number;
  hits: number;      // best_score >= 0.3
  mean_latency: number;
  mean_top_score: number;
}

const SCORE_BINS = [
  { lo: 0.0, hi: 0.1, label: "0.0-0.1" },
  { lo: 0.1, hi: 0.2, label: "0.1-0.2" },
  { lo: 0.2, hi: 0.3, label: "0.2-0.3" },
  { lo: 0.3, hi: 0.4, label: "0.3-0.4" },
  { lo: 0.4, hi: 0.5, label: "0.4-0.5" },
  { lo: 0.5, hi: 0.6, label: "0.5-0.6" },
  { lo: 0.6, hi: 0.7, label: "0.6-0.7" },
  { lo: 0.7, hi: 0.8, label: "0.7-0.8" },
  { lo: 0.8, hi: 0.9, label: "0.8-0.9" },
  { lo: 0.9, hi: 1.01, label: "0.9-1.0" },
];

const LATENCY_BINS = [
  { lt: 100, label: "<100" },
  { lt: 200, label: "100-200" },
  { lt: 400, label: "200-400" },
  { lt: 800, label: "400-800" },
  { lt: 1600, label: "800-1600" },
  { lt: 3200, label: "1600-3200" },
  { lt: Infinity, label: ">3200" },
];

function fmtPct(n: number, digits = 1): string {
  if (!isFinite(n)) return "-";
  return `${(n * 100).toFixed(digits)}%`;
}

function dayKey(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function startOfDay(ts: number): number {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

interface Summary {
  total: number;
  hits: number;            // queries with best_score >= 0.3
  strong: number;          // queries with best_score >= 0.5
  empty: number;           // no candidate at all
  meanLatency: number;
  p95Latency: number;
  meanTopScore: number;
  uniqueTracks: number;
  totalDuration: number;   // sum of query durations sec
}

function computeAll(items: StoredMatch[]) {
  const summary: Summary = {
    total: items.length,
    hits: 0,
    strong: 0,
    empty: 0,
    meanLatency: 0,
    p95Latency: 0,
    meanTopScore: 0,
    uniqueTracks: 0,
    totalDuration: 0,
  };

  const scoreHist: Bucket[] = SCORE_BINS.map(b => ({ label: b.label, count: 0 }));
  const latencyHist: Bucket[] = LATENCY_BINS.map(b => ({ label: b.label, count: 0 }));
  const trackMap = new Map<string, { t: TopTrack; scores: number[] }>();
  const dayMap = new Map<string, DailyPoint & { latencies: number[]; topScores: number[] }>();

  const latencies: number[] = [];
  let scoreSum = 0;
  let scoreSumCount = 0;

  for (const m of items) {
    const best = m.results[0];
    if (!best) {
      summary.empty++;
    } else {
      if (best.score >= 0.3) summary.hits++;
      if (best.score >= 0.5) summary.strong++;
      scoreSum += best.score;
      scoreSumCount++;
      // score hist of best result
      for (let i = 0; i < SCORE_BINS.length; i++) {
        const b = SCORE_BINS[i];
        if (best.score >= b.lo && best.score < b.hi) {
          scoreHist[i].count++;
          break;
        }
      }
    }

    latencies.push(m.elapsed_ms);
    for (let i = 0; i < LATENCY_BINS.length; i++) {
      if (m.elapsed_ms < LATENCY_BINS[i].lt) {
        latencyHist[i].count++;
        break;
      }
    }

    if (m.duration_sec) summary.totalDuration += m.duration_sec;

    for (const r of m.results) {
      const prev = trackMap.get(r.track_id);
      if (prev) {
        prev.t.seen++;
        prev.scores.push(r.score);
        if (r.score > prev.t.best_score) prev.t.best_score = r.score;
      } else {
        trackMap.set(r.track_id, {
          t: {
            track_id: r.track_id,
            title: r.title || "(untitled)",
            artist: r.artist || "",
            source: r.source,
            seen: 1,
            best_score: r.score,
            mean_score: r.score,
          },
          scores: [r.score],
        });
      }
    }

    const k = dayKey(m.ts);
    let day = dayMap.get(k);
    if (!day) {
      day = {
        day: k,
        ts: startOfDay(m.ts),
        queries: 0,
        hits: 0,
        mean_latency: 0,
        mean_top_score: 0,
        latencies: [],
        topScores: [],
      };
      dayMap.set(k, day);
    }
    day.queries++;
    if (best && best.score >= 0.3) day.hits++;
    day.latencies.push(m.elapsed_ms);
    if (best) day.topScores.push(best.score);
  }

  // Finalize tracks
  const tracks: TopTrack[] = [];
  for (const { t, scores } of trackMap.values()) {
    t.mean_score = scores.reduce((s, v) => s + v, 0) / scores.length;
    tracks.push(t);
  }
  tracks.sort((a, b) => b.seen - a.seen || b.best_score - a.best_score);
  summary.uniqueTracks = tracks.length;

  // Latency summary
  if (latencies.length) {
    const sorted = [...latencies].sort((a, b) => a - b);
    const sum = sorted.reduce((s, v) => s + v, 0);
    summary.meanLatency = sum / sorted.length;
    const idx = Math.min(sorted.length - 1, Math.floor(0.95 * (sorted.length - 1)));
    summary.p95Latency = sorted[idx];
  }
  summary.meanTopScore = scoreSumCount ? scoreSum / scoreSumCount : 0;

  // Finalize days, fill gaps
  const dayPoints: DailyPoint[] = [];
  if (dayMap.size) {
    const sortedDays = [...dayMap.values()].sort((a, b) => a.ts - b.ts);
    const first = sortedDays[0].ts;
    const last = sortedDays[sortedDays.length - 1].ts;
    const oneDay = 86_400_000;
    for (let t = first; t <= last; t += oneDay) {
      const k = dayKey(t);
      const d = dayMap.get(k);
      if (d) {
        const mean =
          d.latencies.reduce((s, v) => s + v, 0) / d.latencies.length;
        const meanTop =
          d.topScores.length
            ? d.topScores.reduce((s, v) => s + v, 0) / d.topScores.length
            : 0;
        dayPoints.push({
          day: k.slice(5),
          ts: d.ts,
          queries: d.queries,
          hits: d.hits,
          mean_latency: Math.round(mean),
          mean_top_score: Number(meanTop.toFixed(3)),
        });
      } else {
        dayPoints.push({
          day: dayKey(t).slice(5),
          ts: t,
          queries: 0,
          hits: 0,
          mean_latency: 0,
          mean_top_score: 0,
        });
      }
    }
  }

  return { summary, scoreHist, latencyHist, tracks, dayPoints };
}

function StatCard({
  icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  accent?: "phosphor" | "amber" | "muted";
}) {
  const color =
    accent === "phosphor"
      ? "text-[var(--color-phosphor)]"
      : accent === "amber"
      ? "text-[var(--color-amber)]"
      : "text-[var(--color-text)]";
  return (
    <div className="panel rounded-[2px] p-4 flex flex-col gap-2 min-w-0">
      <div className="flex items-center gap-2 text-[var(--color-muted)]">
        <span className="text-[var(--color-phosphor)]">{icon}</span>
        <span className="font-mono text-[10px] uppercase tracking-widest">{label}</span>
      </div>
      <div className={`font-mono text-2xl tabular-nums ${color}`}>{value}</div>
      {sub ? (
        <div className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
          {sub}
        </div>
      ) : null}
    </div>
  );
}

const TOOLTIP_STYLE: React.CSSProperties = {
  background: "#0B1014",
  border: "1px solid #1A2129",
  borderRadius: 2,
  color: "#E6EBEF",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: 11,
};

const AXIS = { fill: "#6B7682", fontSize: 10, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };

export default function InsightsPage() {
  const [items, setItems] = useState<StoredMatch[] | null>(null);

  useEffect(() => {
    setItems(loadMatches());
  }, []);

  const data = useMemo(() => (items ? computeAll(items) : null), [items]);

  if (items === null) {
    return (
      <div className="px-4 py-8 font-mono text-xs text-[var(--color-muted)]">
        loading insights...
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="px-4 py-4">
        <div className="flex items-end justify-between mb-3">
          <div>
            <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
              insights <span className="text-[var(--color-text)]">/ 0</span>
            </h1>
            <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
              aggregated locally from your query log. nothing leaves the browser.
            </p>
          </div>
        </div>
        <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-dim)]">
          empty / run a capture or try a{" "}
          <Link href="/demo" className="text-[var(--color-phosphor)] hover:underline">
            demo sample
          </Link>{" "}
          to populate
        </div>
      </div>
    );
  }

  const { summary, scoreHist, latencyHist, tracks, dayPoints } = data!;

  const hitRate = summary.total ? summary.hits / summary.total : 0;
  const strongRate = summary.total ? summary.strong / summary.total : 0;

  return (
    <div className="px-4 py-4">
      <div className="flex items-end justify-between mb-4">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            insights <span className="text-[var(--color-text)]">/ {summary.total}</span>
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            aggregated locally from your query log. nothing leaves the browser.
          </p>
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <Link href="/matches" className="hover:text-[var(--color-phosphor)]">matches</Link>
          <span>/</span>
          <Link href="/catalog" className="hover:text-[var(--color-phosphor)]">catalog</Link>
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
        <StatCard
          icon={<MusicNotes size={14} weight="duotone" />}
          label="queries"
          value={summary.total.toLocaleString()}
          sub={`${summary.uniqueTracks} unique tracks`}
        />
        <StatCard
          icon={<Target size={14} weight="duotone" />}
          label="hit rate"
          value={fmtPct(hitRate)}
          sub={`${summary.hits} >= 0.30`}
          accent="phosphor"
        />
        <StatCard
          icon={<Target size={14} weight="duotone" />}
          label="strong hits"
          value={fmtPct(strongRate)}
          sub={`${summary.strong} >= 0.50`}
          accent="phosphor"
        />
        <StatCard
          icon={<ChartBar size={14} weight="duotone" />}
          label="mean top score"
          value={summary.meanTopScore.toFixed(3)}
          sub={`${summary.empty} empty`}
          accent="amber"
        />
        <StatCard
          icon={<Lightning size={14} weight="duotone" />}
          label="latency p95"
          value={`${Math.round(summary.p95Latency)} ms`}
          sub={`mean ${Math.round(summary.meanLatency)} ms`}
          accent="amber"
        />
        <StatCard
          icon={<Clock size={14} weight="duotone" />}
          label="audio sent"
          value={`${summary.totalDuration.toFixed(1)} s`}
          sub="captured + uploaded"
        />
      </div>

      {/* Chart grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-4">
        {/* Score distribution */}
        <div className="panel rounded-[2px] p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <ChartBar size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
              <h2 className="font-mono text-[11px] uppercase tracking-widest">
                top-score distribution
              </h2>
            </div>
            <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
              best result per query
            </span>
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={scoreHist} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="#1A2129" vertical={false} />
                <XAxis dataKey="label" tick={AXIS} axisLine={{ stroke: "#1A2129" }} tickLine={false} />
                <YAxis tick={AXIS} axisLine={{ stroke: "#1A2129" }} tickLine={false} allowDecimals={false} />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v: any) => [Number(v), "queries"]}
                  cursor={{ fill: "rgba(61, 252, 142, 0.06)" }}
                />
                <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                  {scoreHist.map((b, i) => {
                    const lo = SCORE_BINS[i].lo;
                    const fill = lo >= 0.5 ? "#3DFC8E" : lo >= 0.3 ? "#F59E0B" : "#1F8A4D";
                    return <Cell key={i} fill={fill} />;
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Latency distribution */}
        <div className="panel rounded-[2px] p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Lightning size={14} weight="duotone" className="text-[var(--color-amber)]" />
              <h2 className="font-mono text-[11px] uppercase tracking-widest">
                latency distribution
              </h2>
            </div>
            <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
              ms per match
            </span>
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={latencyHist} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="#1A2129" vertical={false} />
                <XAxis dataKey="label" tick={AXIS} axisLine={{ stroke: "#1A2129" }} tickLine={false} />
                <YAxis tick={AXIS} axisLine={{ stroke: "#1A2129" }} tickLine={false} allowDecimals={false} />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v: any) => [Number(v), "queries"]}
                  cursor={{ fill: "rgba(245, 158, 11, 0.06)" }}
                />
                <Bar dataKey="count" fill="#F59E0B" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Activity over time */}
      <div className="panel rounded-[2px] p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <WaveformIcon size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            <h2 className="font-mono text-[11px] uppercase tracking-widest">activity / day</h2>
          </div>
          <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
            queries + hits (&gt;= 0.30)
          </span>
        </div>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={dayPoints} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <defs>
                <linearGradient id="qGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3DFC8E" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="#3DFC8E" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="hGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#F59E0B" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="#F59E0B" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 4" stroke="#1A2129" vertical={false} />
              <XAxis dataKey="day" tick={AXIS} axisLine={{ stroke: "#1A2129" }} tickLine={false} />
              <YAxis tick={AXIS} axisLine={{ stroke: "#1A2129" }} tickLine={false} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ stroke: "#3DFC8E", strokeWidth: 1 }} />
              <Area
                type="monotone"
                dataKey="queries"
                stroke="#3DFC8E"
                strokeWidth={1.2}
                fill="url(#qGrad)"
                name="queries"
              />
              <Area
                type="monotone"
                dataKey="hits"
                stroke="#F59E0B"
                strokeWidth={1.2}
                fill="url(#hGrad)"
                name="hits"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Top tracks */}
      <div className="panel rounded-[2px] overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-line)]">
          <div className="flex items-center gap-2">
            <MusicNotes size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            <h2 className="font-mono text-[11px] uppercase tracking-widest">
              most matched tracks <span className="text-[var(--color-dim)]">/ {tracks.length}</span>
            </h2>
          </div>
          <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
            top 15 by seen count
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="dense-table">
            <thead>
              <tr>
                <th className="w-[40px] text-right">#</th>
                <th>track</th>
                <th className="w-[200px]">artist</th>
                <th className="w-[80px] text-right">seen</th>
                <th className="w-[100px] text-right">best</th>
                <th className="w-[100px] text-right">mean</th>
                <th className="w-[120px]">source</th>
              </tr>
            </thead>
            <tbody>
              {tracks.slice(0, 15).map((t, i) => (
                <tr key={t.track_id}>
                  <td className="font-mono text-right text-[var(--color-dim)] tabular-nums">{i + 1}</td>
                  <td className="text-[var(--color-text)]">{t.title}</td>
                  <td className="text-[var(--color-muted)]">{t.artist || <span className="text-[var(--color-dim)]">-</span>}</td>
                  <td className="font-mono text-right tabular-nums text-[var(--color-phosphor)]">{t.seen}</td>
                  <td className="font-mono text-right tabular-nums">
                    <span className={t.best_score >= 0.5 ? "text-[var(--color-phosphor)]" : t.best_score >= 0.3 ? "text-[var(--color-amber)]" : "text-[var(--color-muted)]"}>
                      {t.best_score.toFixed(3)}
                    </span>
                  </td>
                  <td className="font-mono text-right tabular-nums text-[var(--color-muted)]">{t.mean_score.toFixed(3)}</td>
                  <td className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">{t.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
