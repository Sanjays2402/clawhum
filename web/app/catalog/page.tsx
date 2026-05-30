"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import Spectrogram from "@/components/Spectrogram";
import { deriveCatalog, type CatalogTrack } from "@/lib/history";
import { swrFetcher, type Stats } from "@/lib/api";

export default function CatalogPage() {
  const [items, setItems] = useState<CatalogTrack[] | null>(null);
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<"seen" | "score" | "title">("seen");
  const { data: stats } = useSWR<Stats>("/api/stats", swrFetcher);

  useEffect(() => { setItems(deriveCatalog()); }, []);

  const view = useMemo(() => {
    if (!items) return [];
    let arr = items.filter(t => {
      if (!filter) return true;
      const f = filter.toLowerCase();
      return t.title.toLowerCase().includes(f) || t.artist.toLowerCase().includes(f) || t.track_id.toLowerCase().includes(f);
    });
    arr.sort((a, b) => {
      if (sort === "seen") return b.seen - a.seen;
      if (sort === "score") return b.best_score - a.best_score;
      return a.title.localeCompare(b.title);
    });
    return arr;
  }, [items, filter, sort]);

  return (
    <div className="px-4 py-4">
      <div className="flex items-end justify-between mb-3 gap-4 flex-wrap">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            fingerprinted catalog
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            derived from candidates seen in local query log. authoritative count via index.
          </p>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px]">
          <span className="text-[var(--color-muted)]">index tracks <span className="text-[var(--color-phosphor)] tabular-nums">{stats?.tracks ?? "—"}</span></span>
          <span className="text-[var(--color-muted)]">vectors <span className="text-[var(--color-text)] tabular-nums">{stats?.vectors ?? "—"}</span></span>
          <span className="text-[var(--color-muted)]">backend <span className="text-[var(--color-text)]">{stats?.backend ?? "—"}</span></span>
        </div>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <input
          placeholder="filter / title / artist / track_id"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="w-72"
        />
        <div className="flex border border-[var(--color-line-2)] rounded-[2px] overflow-hidden">
          {(["seen", "score", "title"] as const).map(s => (
            <button
              key={s}
              onClick={() => setSort(s)}
              className={`px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest border-r border-[var(--color-line-2)] last:border-r-0
                ${sort === s ? "bg-[var(--color-phosphor)] text-[#04140A]" : "text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {items === null ? (
        <div className="font-mono text-xs text-[var(--color-muted)]">loading...</div>
      ) : items.length === 0 ? (
        <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-dim)]">
          empty / run captures on the landing page to surface tracks
          <div className="mt-3">
            <Link href="/" className="text-[var(--color-phosphor)] hover:underline">→ open capture</Link>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {view.map(t => (
            <div key={t.track_id} className="panel rounded-[2px] p-3 hover:border-[var(--color-phosphor-dim)] transition">
              <div className="flex items-start gap-3">
                {t.artwork_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={t.artwork_url} alt="" className="w-12 h-12 rounded-[2px] object-cover border border-[var(--color-line)]" />
                ) : (
                  <div className="w-12 h-12 rounded-[2px] panel-inset flex items-center justify-center font-mono text-[10px] text-[var(--color-dim)]">
                    {t.source.slice(0, 3)}
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{t.title || <span className="text-[var(--color-dim)]">untitled</span>}</div>
                  <div className="truncate font-mono text-[10px] text-[var(--color-muted)]">{t.artist || "—"}</div>
                  <div className="truncate font-mono text-[9px] text-[var(--color-dim)] mt-0.5">{t.track_id}</div>
                </div>
              </div>
              <div className="mt-3 -mx-3 -mb-3">
                <Spectrogram height={36} seed={t.track_id} />
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[10px]">
                <Cell k="seen" v={String(t.seen)} />
                <Cell k="best" v={t.best_score.toFixed(3)} accent />
                <Cell k="bpm" v={t.tempo_bpm ? t.tempo_bpm.toFixed(0) : "—"} />
              </div>
              {t.preview_url && (
                <audio controls src={t.preview_url} className="w-full h-7 mt-2" />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Cell({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="panel-inset px-2 py-1 rounded-[1px]">
      <div className="text-[9px] uppercase tracking-widest text-[var(--color-dim)]">{k}</div>
      <div className={`tabular-nums ${accent ? "text-[var(--color-phosphor)]" : "text-[var(--color-text)]"}`}>{v}</div>
    </div>
  );
}
