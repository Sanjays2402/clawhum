"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";
import {
  ThumbsUp,
  ThumbsDown,
  Download,
  Funnel,
  ArrowsClockwise,
  MusicNotes,
} from "@phosphor-icons/react/dist/ssr";
import { swrFetcher } from "@/lib/api";

type Vote = -1 | 0 | 1;

interface FeedbackRow {
  ts: number;
  query_id: string;
  track_id: string;
  score: number;
  vote: Vote;
  tenant_id?: string;
}

interface FeedbackResponse {
  tenant_id: string;
  total: number;
  limit: number;
  offset: number;
  summary: {
    confirm: number;
    reject: number;
    neutral: number;
    unique_queries: number;
    unique_tracks: number;
  };
  rows: FeedbackRow[];
}

const PAGE = 200;

function fmtScore(s: number): string {
  if (typeof s !== "number" || !isFinite(s)) return "—";
  return s.toFixed(3);
}

function fmtTime(ts: number): string {
  if (!ts) return "—";
  // server writes seconds; allow either
  const ms = ts < 1e12 ? ts * 1000 : ts;
  const d = new Date(ms);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function buildQuery(vote: Vote | "all", track: string, offset: number): string {
  const p = new URLSearchParams();
  p.set("limit", String(PAGE));
  p.set("offset", String(offset));
  if (vote !== "all") p.set("vote", String(vote));
  if (track) p.set("track_id", track);
  return `/api/feedback?${p.toString()}`;
}

function buildTriplets(rows: FeedbackRow[]): {
  triplets: Array<{ anchor: string; positive: string; negative: string }>;
  pairs: { positives: number; negatives: number; queries: number };
} {
  const byQuery = new Map<string, { pos: FeedbackRow[]; neg: FeedbackRow[] }>();
  for (const r of rows) {
    const bucket = byQuery.get(r.query_id) ?? { pos: [], neg: [] };
    if (r.vote === 1) bucket.pos.push(r);
    else if (r.vote === -1) bucket.neg.push(r);
    byQuery.set(r.query_id, bucket);
  }
  const triplets: Array<{ anchor: string; positive: string; negative: string }> = [];
  let positives = 0;
  let negatives = 0;
  for (const [qid, b] of byQuery) {
    positives += b.pos.length;
    negatives += b.neg.length;
    for (const p of b.pos) {
      for (const n of b.neg) {
        triplets.push({ anchor: qid, positive: p.track_id, negative: n.track_id });
      }
    }
  }
  return { triplets, pairs: { positives, negatives, queries: byQuery.size } };
}

function exportTriplets(rows: FeedbackRow[]) {
  const { triplets } = buildTriplets(rows);
  if (triplets.length === 0) {
    alert("no triplets yet / need at least one confirm and one reject on the same query");
    return;
  }
  const lines = triplets.map((t) => JSON.stringify(t)).join("\n") + "\n";
  const blob = new Blob([lines], { type: "application/x-ndjson" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `clawhum-triplets-${new Date().toISOString().slice(0, 10)}.jsonl`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function FeedbackPage() {
  const [vote, setVote] = useState<Vote | "all">("all");
  const [track, setTrack] = useState("");
  const [offset, setOffset] = useState(0);

  const key = buildQuery(vote, track.trim(), offset);
  const { data, error, isLoading, mutate } = useSWR<FeedbackResponse>(key, swrFetcher, {
    revalidateOnFocus: false,
  });

  const chartData = useMemo(() => {
    const s = data?.summary;
    if (!s) return [];
    return [
      { label: "confirm", count: s.confirm, color: "var(--color-phosphor)" },
      { label: "reject", count: s.reject, color: "#a35c5c" },
      { label: "neutral", count: s.neutral, color: "var(--color-muted)" },
    ];
  }, [data]);

  const { triplets, pairs } = useMemo(
    () => buildTriplets(data?.rows ?? []),
    [data?.rows],
  );

  const total = data?.total ?? 0;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + PAGE, total);

  return (
    <div className="px-4 py-4 space-y-4">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            feedback / review queue + triplet export
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            every confirm or reject you submit from a match detail page lands here. export pairs as a triplet-loss corpus for embedder fine-tuning.
          </p>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px]">
          <span className="text-[var(--color-muted)]">
            tenant <span className="text-[var(--color-text)]">{data?.tenant_id ?? "—"}</span>
          </span>
          <button
            className="panel rounded-[2px] px-2 py-1 flex items-center gap-1 text-[var(--color-muted)] hover:text-[var(--color-text)]"
            onClick={() => mutate()}
            aria-label="refresh"
          >
            <ArrowsClockwise size={12} weight="duotone" />
            refresh
          </button>
        </div>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="confirm" value={data?.summary.confirm ?? 0} accent="var(--color-phosphor)" icon={<ThumbsUp size={14} weight="duotone" />} />
        <Tile label="reject" value={data?.summary.reject ?? 0} accent="#a35c5c" icon={<ThumbsDown size={14} weight="duotone" />} />
        <Tile label="queries" value={data?.summary.unique_queries ?? 0} icon={<Funnel size={14} weight="duotone" />} />
        <Tile label="tracks" value={data?.summary.unique_tracks ?? 0} icon={<MusicNotes size={14} weight="duotone" />} />
      </div>

      {/* Chart + export */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="panel rounded-[2px] p-3 lg:col-span-2">
          <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] mb-2">
            vote distribution
          </div>
          <div style={{ width: "100%", height: 180 }}>
            <ResponsiveContainer>
              <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }} stroke="var(--color-line)" />
                <YAxis tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }} stroke="var(--color-line)" allowDecimals={false} />
                <Tooltip
                  cursor={{ fill: "rgba(255,255,255,0.03)" }}
                  contentStyle={{ background: "var(--color-bg)", border: "1px solid var(--color-line)", fontSize: 11, fontFamily: "var(--font-mono)" }}
                />
                <Bar dataKey="count" radius={[1, 1, 0, 0]}>
                  {chartData.map((d, i) => (
                    <Cell key={i} fill={d.color || "var(--color-phosphor)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="panel rounded-[2px] p-3 flex flex-col gap-2">
          <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
            triplet export
          </div>
          <p className="font-mono text-[11px] text-[var(--color-text)]">
            <span className="text-[var(--color-phosphor)] tabular-nums">{triplets.length}</span> triplets / built from <span className="tabular-nums">{pairs.queries}</span> queries
            (<span className="tabular-nums">{pairs.positives}</span> positives, <span className="tabular-nums">{pairs.negatives}</span> negatives)
          </p>
          <p className="font-mono text-[10px] text-[var(--color-muted)]">
            anchor / positive / negative pairs are derived per query_id. each confirmed track pairs with each rejected track in the same query.
          </p>
          <button
            className="mt-auto panel rounded-[2px] px-3 py-2 flex items-center justify-center gap-2 text-[11px] font-mono text-[var(--color-text)] hover:bg-[rgba(255,255,255,0.03)] disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={() => exportTriplets(data?.rows ?? [])}
            disabled={triplets.length === 0}
          >
            <Download size={12} weight="duotone" />
            export {triplets.length} triplets / jsonl
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="panel rounded-[2px] px-4 py-3 flex items-center gap-6 flex-wrap font-mono text-[11px]">
        <div className="flex items-center gap-2">
          <span className="text-[var(--color-muted)]">vote</span>
          <div className="flex border border-[var(--color-line)] rounded-[2px] overflow-hidden">
            {(["all", 1, -1, 0] as const).map((v) => (
              <button
                key={String(v)}
                onClick={() => { setVote(v as Vote | "all"); setOffset(0); }}
                className={`px-2 py-1 ${vote === v ? "bg-[var(--color-phosphor)] text-black" : "text-[var(--color-muted)] hover:text-[var(--color-text)]"}`}
                aria-pressed={vote === v}
              >
                {v === "all" ? "all" : v === 1 ? "+1" : v === -1 ? "-1" : "0"}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[var(--color-muted)]">track_id</span>
          <input
            value={track}
            onChange={(e) => { setTrack(e.target.value); setOffset(0); }}
            placeholder="filter / exact track_id"
            className="bg-transparent border border-[var(--color-line)] rounded-[2px] px-2 py-1 text-[var(--color-text)] w-64 placeholder:text-[var(--color-dim)]"
          />
        </div>
        <div className="ml-auto text-[var(--color-muted)]">
          {total === 0
            ? "0 rows"
            : <>showing <span className="text-[var(--color-text)] tabular-nums">{pageStart}-{pageEnd}</span> of <span className="text-[var(--color-text)] tabular-nums">{total}</span></>}
        </div>
      </div>

      {/* Table */}
      <div className="panel rounded-[2px] overflow-hidden">
        <div className="grid grid-cols-[110px_90px_1fr_1fr_80px] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] px-4 py-2 border-b border-[var(--color-line)]">
          <div>vote</div>
          <div className="tabular-nums">score</div>
          <div>track_id</div>
          <div>query_id</div>
          <div className="text-right">time</div>
        </div>

        {error && (
          <div className="px-4 py-6 font-mono text-[11px] text-[#d28080]">
            failed to load / {(error as Error).message}
          </div>
        )}

        {isLoading && !data && (
          <div className="divide-y divide-[var(--color-line)]">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="grid grid-cols-[110px_90px_1fr_1fr_80px] px-4 py-3 gap-2">
                {Array.from({ length: 5 }).map((__, j) => (
                  <div key={j} className="h-3 bg-[rgba(255,255,255,0.04)] rounded-[1px] animate-pulse" />
                ))}
              </div>
            ))}
          </div>
        )}

        {!isLoading && data && data.rows.length === 0 && (
          <div className="px-4 py-10 text-center font-mono text-[11px] text-[var(--color-dim)] space-y-2">
            <div>no feedback yet for this tenant.</div>
            <div>
              capture a hum on <Link href="/" className="text-[var(--color-phosphor)] hover:underline">/</Link>,
              open a match, then vote +1 or -1 on each candidate.
            </div>
          </div>
        )}

        {data && data.rows.length > 0 && (
          <div className="divide-y divide-[var(--color-line)]">
            {data.rows.map((r, i) => (
              <Link
                key={`${r.query_id}-${r.track_id}-${r.ts}-${i}`}
                href={`/matches/${encodeURIComponent(r.query_id)}`}
                className="grid grid-cols-[110px_90px_1fr_1fr_80px] px-4 py-2 gap-2 items-center font-mono text-[11px] text-[var(--color-text)] hover:bg-[rgba(255,255,255,0.025)]"
              >
                <div className="flex items-center gap-1.5">
                  {r.vote === 1 ? (
                    <span className="text-[var(--color-phosphor)] flex items-center gap-1"><ThumbsUp size={12} weight="duotone" /> confirm</span>
                  ) : r.vote === -1 ? (
                    <span className="text-[#d28080] flex items-center gap-1"><ThumbsDown size={12} weight="duotone" /> reject</span>
                  ) : (
                    <span className="text-[var(--color-muted)]">neutral</span>
                  )}
                </div>
                <div className="tabular-nums">{fmtScore(r.score)}</div>
                <div className="truncate text-[var(--color-text)]">{r.track_id}</div>
                <div className="truncate text-[var(--color-muted)]">{r.query_id}</div>
                <div className="text-right text-[var(--color-muted)] text-[10px]">{fmtTime(r.ts)}</div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Pager */}
      {total > PAGE && (
        <div className="flex items-center justify-between font-mono text-[11px]">
          <button
            className="panel rounded-[2px] px-3 py-1 text-[var(--color-muted)] hover:text-[var(--color-text)] disabled:opacity-30 disabled:cursor-not-allowed"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
            disabled={offset === 0}
          >
            prev
          </button>
          <div className="text-[var(--color-muted)]">page {Math.floor(offset / PAGE) + 1} of {Math.max(1, Math.ceil(total / PAGE))}</div>
          <button
            className="panel rounded-[2px] px-3 py-1 text-[var(--color-muted)] hover:text-[var(--color-text)] disabled:opacity-30 disabled:cursor-not-allowed"
            onClick={() => setOffset((o) => o + PAGE)}
            disabled={offset + PAGE >= total}
          >
            next
          </button>
        </div>
      )}
    </div>
  );
}

function Tile({
  label,
  value,
  accent,
  icon,
}: {
  label: string;
  value: number;
  accent?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="panel rounded-[2px] p-3">
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
        {icon}
        {label}
      </div>
      <div
        className="font-mono tabular-nums text-[22px] mt-1"
        style={{ color: accent || "var(--color-text)" }}
      >
        {value}
      </div>
    </div>
  );
}
