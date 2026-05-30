"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { LineChart, Line, ResponsiveContainer, YAxis, Tooltip } from "recharts";
import { parseProm, type PromMetric } from "@/lib/prom";
import { textFetch } from "@/lib/api";

const POLL_MS = 2000;
const HISTORY = 60;

interface Series {
  name: string;
  unit?: string;
  type?: string;
  help?: string;
  history: { t: number; v: number }[];
  current: number;
  isCounter: boolean;
  rate?: number;
}

function flatKey(name: string, labels: Record<string, string>): string {
  const keys = Object.keys(labels).sort();
  if (!keys.length) return name;
  return `${name}{${keys.map(k => `${k}="${labels[k]}"`).join(",")}}`;
}

export default function MetricsPage() {
  const [raw, setRaw] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const seriesRef = useRef<Map<string, Series>>(new Map());
  const [, force] = useState(0);

  const { data, error } = useSWR("/api/metrics", textFetch, { refreshInterval: POLL_MS });

  useEffect(() => {
    if (error) { setErr(String(error.message || error)); return; }
    if (typeof data !== "string") return;
    setErr(null);
    setRaw(data);
    const parsed = parseProm(data);
    const now = Date.now();
    const map = seriesRef.current;
    for (const m of parsed) {
      for (const s of m.samples) {
        const k = flatKey(s.name, s.labels);
        let series = map.get(k);
        if (!series) {
          series = {
            name: k,
            type: m.type,
            help: m.help,
            isCounter: m.type === "counter",
            history: [],
            current: s.value,
          };
          map.set(k, series);
        }
        series.current = s.value;
        series.type = m.type;
        series.help = m.help;
        series.history.push({ t: now, v: s.value });
        while (series.history.length > HISTORY) series.history.shift();
        if (series.isCounter && series.history.length >= 2) {
          const a = series.history[series.history.length - 2];
          const b = series.history[series.history.length - 1];
          const dt = (b.t - a.t) / 1000;
          series.rate = dt > 0 ? (b.v - a.v) / dt : 0;
        }
      }
    }
    force(x => x + 1);
  }, [data, error]);

  const all = Array.from(seriesRef.current.values()).sort((a, b) => a.name.localeCompare(b.name));
  const top = all.slice(0, 24);

  return (
    <div className="px-4 py-4">
      <div className="flex items-end justify-between mb-3">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            prometheus / live <span className="text-[var(--color-text)]">/ {all.length}</span> series
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            polling /metrics every {POLL_MS}ms / sparkline window {HISTORY} samples
          </p>
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px]">
          <span className="led-dot" />
          <span className="uppercase tracking-widest text-[var(--color-phosphor)]">scraping</span>
        </div>
      </div>

      {err && (
        <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] px-3 py-2 text-[var(--color-amber)] font-mono text-xs mb-3">
          metrics fetch failed / {err}
        </div>
      )}

      {!raw && !err && (
        <div className="font-mono text-xs text-[var(--color-muted)]">waiting for first scrape...</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {top.map(s => (
          <div key={s.name} className="panel rounded-[2px] p-3">
            <div className="flex items-baseline justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[11px] text-[var(--color-text)] truncate">{s.name}</div>
                {s.help && <div className="font-mono text-[9px] text-[var(--color-dim)] truncate mt-0.5">{s.help}</div>}
              </div>
              <div className="text-right shrink-0">
                <div className={`font-mono text-[16px] tabular-nums ${s.isCounter ? "text-[var(--color-phosphor)]" : "text-[var(--color-text)]"}`}>
                  {fmtNum(s.current)}
                </div>
                {s.isCounter && s.rate !== undefined && (
                  <div className="font-mono text-[9px] text-[var(--color-amber)]">
                    {s.rate >= 0 ? "+" : ""}{fmtNum(s.rate)}<span className="text-[var(--color-dim)]">/s</span>
                  </div>
                )}
              </div>
            </div>
            <div className="h-12 mt-2 -mx-1">
              <ResponsiveContainer>
                <LineChart data={s.history} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
                  <YAxis hide domain={["dataMin", "dataMax"]} />
                  <Tooltip
                    contentStyle={{ background: "#06090C", border: "1px solid #232C36", fontFamily: "var(--font-plex-mono)", fontSize: 10, color: "#E6EBEF" }}
                    labelFormatter={(t) => new Date(t as number).toLocaleTimeString()}
                    formatter={(v: any) => [fmtNum(v as number), "value"]}
                  />
                  <Line
                    type="monotone"
                    dataKey="v"
                    stroke={s.isCounter ? "#3DFC8E" : "#FF3DBE"}
                    strokeWidth={1.25}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="flex justify-between font-mono text-[9px] text-[var(--color-dim)] uppercase tracking-widest mt-1">
              <span>{s.type || "untyped"}</span>
              <span>{s.history.length} pts</span>
            </div>
          </div>
        ))}
      </div>

      {raw && (
        <details className="mt-6 panel rounded-[2px] overflow-hidden">
          <summary className="px-3 py-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] cursor-pointer hover:text-[var(--color-phosphor)]">
            raw exposition
          </summary>
          <pre className="px-3 py-2 font-mono text-[10px] text-[var(--color-muted)] overflow-auto max-h-96 bg-[var(--color-bg)]">
{raw}
          </pre>
        </details>
      )}
    </div>
  );
}

function fmtNum(n: number): string {
  if (!isFinite(n)) return n > 0 ? "+inf" : n < 0 ? "-inf" : "nan";
  const abs = Math.abs(n);
  if (abs === 0) return "0";
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + "G";
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(2) + "k";
  if (abs >= 1) return n.toFixed(2);
  if (abs >= 0.01) return n.toFixed(4);
  return n.toExponential(2);
}
