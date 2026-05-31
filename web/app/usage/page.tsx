"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Gauge,
  ChartLineUp,
  Lightning,
  ArrowsClockwise,
  Warning,
  CheckCircle,
  Sparkle,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey } from "@/lib/apiKey";

interface WindowCount {
  total: number;
  window_sec: number;
  by_event?: Record<string, number>;
  percent_used?: number;
  remaining?: number;
}

interface UsagePayload {
  tenant_id: string;
  now: number;
  quota_per_month: number;
  minute: WindowCount;
  day: WindowCount;
  month: WindowCount;
  daily_buckets: number[];
}

type UsageState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: UsagePayload }
  | { kind: "error"; status: number; message: string };

const EVENT_LABELS: Record<string, string> = {
  match: "match",
  batch: "batch",
  pitch: "pitch",
  share: "share",
  history: "history",
  webhook: "webhook",
};

function fmtNum(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

function dayLabel(daysAgo: number, now: number): string {
  const d = new Date(now * 1000 - daysAgo * 86_400_000);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${m}/${day}`;
}

function Sparkline({ values, now }: { values: number[]; now: number }) {
  const max = Math.max(1, ...values);
  const width = 100;
  const height = 24;
  const step = width / Math.max(1, values.length - 1);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="w-full h-12"
      role="img"
      aria-label="Daily usage over the last 30 days"
    >
      {values.map((v, i) => {
        const h = (v / max) * (height - 2);
        const x = i * step;
        return (
          <rect
            key={i}
            x={x - 1}
            y={height - h}
            width={Math.max(1.2, step - 0.8)}
            height={h}
            fill="var(--color-phosphor)"
            opacity={v === 0 ? 0.15 : 0.85}
          >
            <title>{`${dayLabel(values.length - 1 - i, now)} / ${fmtNum(v)}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

function Meter({ percent }: { percent: number }) {
  const pct = Math.max(0, Math.min(100, percent));
  const tone =
    pct >= 100
      ? "bg-red-500"
      : pct >= 80
        ? "bg-amber-400"
        : "bg-[var(--color-phosphor)]";
  return (
    <div className="w-full h-2 bg-[var(--color-bg)] border border-[var(--color-line)] overflow-hidden">
      <div
        className={`h-full ${tone} transition-[width] duration-500`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export default function UsagePage() {
  const [state, setState] = useState<UsageState>({ kind: "idle" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const headers: Record<string, string> = {};
      const k = getApiKey();
      if (k) headers["X-API-Key"] = k;
      const r = await fetch("/api/usage", { headers, cache: "no-store" });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        setState({ kind: "error", status: r.status, message: text || r.statusText });
        return;
      }
      const data = (await r.json()) as UsagePayload;
      setState({ kind: "ok", data });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  return (
    <div className="px-4 py-4 space-y-4 max-w-[1100px]">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
          usage / billable activity
        </h1>
        <button
          type="button"
          onClick={load}
          className="flex items-center gap-1 border border-[var(--color-line)] px-2 py-1 hover:bg-[var(--color-panel)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]"
          aria-label="Refresh usage"
        >
          <ArrowsClockwise size={12} weight="duotone" />
          refresh
        </button>
      </div>

      {state.kind === "loading" && (
        <div className="panel rounded-[2px] p-4 space-y-3 animate-pulse">
          <div className="h-3 w-32 bg-[var(--color-line)]" />
          <div className="h-2 w-full bg-[var(--color-line)]" />
          <div className="h-2 w-2/3 bg-[var(--color-line)]" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="panel rounded-[2px] p-4 flex items-start gap-2">
          <Warning size={16} weight="duotone" className="text-red-400 mt-0.5" />
          <div className="space-y-1">
            <div className="font-mono text-[12px] text-[var(--color-text)]">
              could not load usage
            </div>
            <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
              status {state.status} / {state.message || "unknown"}
            </div>
            {state.status === 401 && (
              <Link
                href="/settings"
                className="inline-flex items-center gap-1 text-[var(--color-phosphor)] font-mono text-[11px] uppercase tracking-widest hover:underline"
              >
                set api key in settings
              </Link>
            )}
          </div>
        </div>
      )}

      {state.kind === "ok" && (
        <UsageView data={state.data} />
      )}

      {state.kind === "idle" && (
        <div className="panel rounded-[2px] p-6 text-center space-y-2">
          <Gauge size={28} weight="duotone" className="text-[var(--color-dim)] mx-auto" />
          <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-dim)]">
            no usage data yet
          </div>
        </div>
      )}
    </div>
  );
}

function UsageView({ data }: { data: UsagePayload }) {
  const pct = data.month.percent_used ?? 0;
  const remaining = data.month.remaining ?? Math.max(0, data.quota_per_month - data.month.total);
  const overQuota = data.month.total >= data.quota_per_month;
  const byEvent = data.month.by_event || {};
  const eventEntries = Object.entries(byEvent).sort((a, b) => b[1] - a[1]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <section className="panel rounded-[2px] p-4 space-y-3 lg:col-span-2">
        <div className="flex items-center gap-2">
          <Gauge size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
          <span className="label-xs">monthly quota</span>
          <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
            tenant / {data.tenant_id}
          </span>
        </div>

        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] text-[var(--color-text)] tabular-nums">
            {fmtNum(data.month.total)}
          </span>
          <span className="font-mono text-[12px] text-[var(--color-dim)] uppercase tracking-widest">
            / {fmtNum(data.quota_per_month)} requests
          </span>
        </div>

        <Meter percent={pct} />

        <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <span>{pct.toFixed(1)}% used</span>
          <span>{fmtNum(remaining)} remaining / resets in 30d window</span>
        </div>

        {overQuota ? (
          <div className="border border-red-500/40 bg-red-500/5 px-3 py-2 flex items-start gap-2">
            <Warning size={14} weight="duotone" className="text-red-400 mt-0.5" />
            <div className="space-y-1">
              <div className="font-mono text-[11px] text-[var(--color-text)] uppercase tracking-widest">
                free tier exhausted
              </div>
              <div className="font-mono text-[10px] text-[var(--color-dim)]">
                upgrade to keep matching. existing data stays intact.
              </div>
            </div>
            <Link
              href="/settings"
              className="ml-auto self-center inline-flex items-center gap-1 border border-red-400/60 text-red-300 px-2 py-1 font-mono text-[10px] uppercase tracking-widest hover:bg-red-500/10"
            >
              <Sparkle size={12} weight="duotone" /> upgrade
            </Link>
          </div>
        ) : pct >= 80 ? (
          <div className="border border-amber-400/40 bg-amber-400/5 px-3 py-2 flex items-start gap-2">
            <Warning size={14} weight="duotone" className="text-amber-300 mt-0.5" />
            <div className="space-y-1">
              <div className="font-mono text-[11px] text-[var(--color-text)] uppercase tracking-widest">
                approaching limit
              </div>
              <div className="font-mono text-[10px] text-[var(--color-dim)]">
                you have used {pct.toFixed(0)}% of your monthly quota.
              </div>
            </div>
            <Link
              href="/settings"
              className="ml-auto self-center inline-flex items-center gap-1 border border-amber-400/60 text-amber-200 px-2 py-1 font-mono text-[10px] uppercase tracking-widest hover:bg-amber-400/10"
            >
              <Sparkle size={12} weight="duotone" /> upgrade
            </Link>
          </div>
        ) : (
          <div className="border border-[var(--color-line)] px-3 py-2 flex items-center gap-2">
            <CheckCircle size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
              within free tier
            </span>
          </div>
        )}
      </section>

      <section className="panel rounded-[2px] p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Lightning size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
          <span className="label-xs">activity windows</span>
        </div>
        <Stat label="last minute" value={data.minute.total} />
        <Stat label="last 24 hours" value={data.day.total} />
        <Stat label="last 30 days" value={data.month.total} />
      </section>

      <section className="panel rounded-[2px] p-4 space-y-3 lg:col-span-2">
        <div className="flex items-center gap-2">
          <ChartLineUp size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
          <span className="label-xs">last 30 days</span>
          <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
            {fmtNum(Math.max(...data.daily_buckets))} peak / day
          </span>
        </div>
        <Sparkline values={data.daily_buckets} now={data.now} />
        <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <span>{dayLabel(29, data.now)}</span>
          <span>{dayLabel(0, data.now)}</span>
        </div>
      </section>

      <section className="panel rounded-[2px] p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Gauge size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
          <span className="label-xs">by event / 30d</span>
        </div>
        {eventEntries.length === 0 ? (
          <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)] py-4 text-center">
            no chargeable events yet
          </div>
        ) : (
          <ul className="space-y-1.5">
            {eventEntries.map(([ev, count]) => {
              const share = data.month.total > 0 ? (count / data.month.total) * 100 : 0;
              return (
                <li key={ev} className="space-y-1">
                  <div className="flex items-center justify-between font-mono text-[11px]">
                    <span className="text-[var(--color-text)] uppercase tracking-widest">
                      {EVENT_LABELS[ev] || ev}
                    </span>
                    <span className="tabular-nums text-[var(--color-muted)]">{fmtNum(count)}</span>
                  </div>
                  <div className="h-1 bg-[var(--color-bg)] border border-[var(--color-line)]">
                    <div
                      className="h-full bg-[var(--color-phosphor)]/70"
                      style={{ width: `${share}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
        {label}
      </span>
      <span className="font-mono text-[16px] text-[var(--color-text)] tabular-nums">
        {fmtNum(value)}
      </span>
    </div>
  );
}
