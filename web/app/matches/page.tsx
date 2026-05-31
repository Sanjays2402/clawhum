"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Spectrogram from "@/components/Spectrogram";
import { clearMatches, loadMatches, type StoredMatch } from "@/lib/history";
import ExportMenu from "@/components/ExportMenu";

function fmtRelative(ms: number): string {
  const d = Date.now() - ms;
  if (d < 60_000) return `${Math.round(d / 1000)}s ago`;
  if (d < 3_600_000) return `${Math.round(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.round(d / 3_600_000)}h ago`;
  return `${Math.round(d / 86_400_000)}d ago`;
}

function fmtTs(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export default function MatchesPage() {
  const [items, setItems] = useState<StoredMatch[] | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => { setItems(loadMatches()); }, []);

  if (items === null) {
    return (
      <div className="px-4 py-8 font-mono text-xs text-[var(--color-muted)]">
        loading query log...
      </div>
    );
  }

  const filtered = items.filter(m => {
    if (!filter) return true;
    const f = filter.toLowerCase();
    return (
      m.query_id.toLowerCase().includes(f) ||
      m.filename?.toLowerCase().includes(f) ||
      m.results.some(r => r.title.toLowerCase().includes(f) || r.artist.toLowerCase().includes(f))
    );
  });

  return (
    <div className="px-4 py-4">
      <div className="flex items-end justify-between mb-3">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            query log <span className="text-[var(--color-text)]">/ {items.length}</span>
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            local. queries are not persisted on the api. clear to reset.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            placeholder="filter / id / track / artist"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="w-72"
          />
          <ExportMenu matches={filtered} />
          <button
            onClick={() => { if (confirm("clear local query log?")) { clearMatches(); setItems([]); } }}
            className="btn-ghost px-3 py-1.5 rounded-[2px] font-mono text-[11px] uppercase tracking-widest"
          >
            clear
          </button>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-dim)]">
          empty / run a capture on the landing page to populate
        </div>
      ) : (
        <div className="panel rounded-[2px] overflow-hidden">
          <table className="dense-table">
            <thead>
              <tr>
                <th className="w-[120px]">query_id</th>
                <th className="w-[150px]">ts</th>
                <th>best match</th>
                <th className="w-[200px]">artist</th>
                <th className="w-[260px]">query fingerprint</th>
                <th className="w-[80px] text-right">score</th>
                <th className="w-[80px] text-right">cand</th>
                <th className="w-[90px] text-right">latency</th>
                <th className="w-[80px] text-right">dur</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(m => {
                const best = m.results[0];
                return (
                  <tr key={m.query_id} className="cursor-pointer">
                    <td className="font-mono text-[var(--color-phosphor)]">
                      <Link href={`/matches/${m.query_id}`} className="hover:underline">{m.query_id.slice(0, 8)}</Link>
                    </td>
                    <td className="font-mono text-[var(--color-muted)] tabular-nums" title={fmtTs(m.ts)}>
                      {fmtRelative(m.ts)}
                    </td>
                    <td>
                      <Link href={`/matches/${m.query_id}`} className="hover:text-[var(--color-phosphor)]">
                        {best ? (best.title || <span className="text-[var(--color-dim)]">untitled</span>) : <span className="text-[var(--color-dim)]">no candidate</span>}
                      </Link>
                    </td>
                    <td className="text-[var(--color-muted)]">{best?.artist || <span className="text-[var(--color-dim)]">—</span>}</td>
                    <td><Spectrogram height={28} seed={m.query_id} /></td>
                    <td className="font-mono text-right tabular-nums">
                      {best ? (
                        <span className={best.score >= 0.5 ? "text-[var(--color-phosphor)]" : best.score >= 0.3 ? "text-[var(--color-amber)]" : "text-[var(--color-muted)]"}>
                          {best.score.toFixed(4)}
                        </span>
                      ) : <span className="text-[var(--color-dim)]">—</span>}
                    </td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-muted)]">{m.count}</td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-phosphor)]">{m.elapsed_ms}<span className="text-[var(--color-dim)] ml-0.5">ms</span></td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-muted)]">{m.duration_sec ? m.duration_sec.toFixed(1) + "s" : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="py-10 text-center font-mono text-xs text-[var(--color-dim)]">no rows match filter</div>
          )}
        </div>
      )}
    </div>
  );
}
