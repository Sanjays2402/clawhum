"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import Spectrogram from "@/components/Spectrogram";
import { swrFetcher, type Stats } from "@/lib/api";
import { MagnifyingGlass, ArrowLeft, ArrowRight, MusicNotes, Database } from "@phosphor-icons/react";

type TrackSummary = {
  id: string;
  title: string;
  artist: string;
  album: string;
  duration_s: number;
  source: string;
  tempo_bpm: number | null;
  key: string | null;
  preview_url: string | null;
  artwork_url: string | null;
  has_audio: boolean;
};
type TracksList = { items: TrackSummary[]; total: number; limit: number; offset: number };

type SortKey = "title" | "artist" | "duration" | "id";

const PAGE = 24;

export default function CatalogPage() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [sort, setSort] = useState<SortKey>("title");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [source, setSource] = useState<string>("");
  const [offset, setOffset] = useState(0);

  // Debounce search.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q.trim()), 200);
    return () => clearTimeout(id);
  }, [q]);

  // Reset paging when filters change.
  useEffect(() => { setOffset(0); }, [debouncedQ, sort, order, source]);

  const { data: stats } = useSWR<Stats>("/api/stats", swrFetcher);

  const url = useMemo(() => {
    const u = new URL("/api/tracks", "http://x");
    u.searchParams.set("limit", String(PAGE));
    u.searchParams.set("offset", String(offset));
    u.searchParams.set("sort", sort);
    u.searchParams.set("order", order);
    if (debouncedQ) u.searchParams.set("q", debouncedQ);
    if (source) u.searchParams.set("source", source);
    return u.pathname + u.search;
  }, [debouncedQ, sort, order, source, offset]);

  const { data, error, isLoading } = useSWR<TracksList>(url, swrFetcher, {
    keepPreviousData: true,
  });

  const sources = useMemo(() => {
    const s = new Set<string>();
    data?.items.forEach(t => { if (t.source) s.add(t.source); });
    return Array.from(s).sort();
  }, [data]);

  const total = data?.total ?? 0;
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + PAGE, total);
  const canPrev = offset > 0;
  const canNext = offset + PAGE < total;

  return (
    <div className="px-4 py-4">
      <div className="flex items-end justify-between mb-3 gap-4 flex-wrap">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            indexed catalog
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            every fingerprinted track on the server. search, sort, page.
          </p>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px]">
          <span className="text-[var(--color-muted)]">index tracks <span className="text-[var(--color-phosphor)] tabular-nums">{stats?.tracks ?? "—"}</span></span>
          <span className="text-[var(--color-muted)]">vectors <span className="text-[var(--color-text)] tabular-nums">{stats?.vectors ?? "—"}</span></span>
          <span className="text-[var(--color-muted)]">backend <span className="text-[var(--color-text)]">{stats?.backend ?? "—"}</span></span>
        </div>
      </div>

      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <label className="flex items-center gap-2 panel-inset rounded-[2px] px-2 py-1.5 w-72">
          <MagnifyingGlass size={14} weight="duotone" className="text-[var(--color-dim)]" />
          <input
            placeholder="search title, artist, album, id"
            value={q}
            onChange={e => setQ(e.target.value)}
            className="bg-transparent outline-none flex-1 font-mono text-[12px] placeholder:text-[var(--color-dim)]"
            aria-label="search catalog"
          />
        </label>

        <div className="flex border border-[var(--color-line-2)] rounded-[2px] overflow-hidden">
          {(["title", "artist", "duration", "id"] as SortKey[]).map(s => (
            <button
              key={s}
              onClick={() => setSort(s)}
              className={`px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest border-r border-[var(--color-line-2)] last:border-r-0
                ${sort === s ? "bg-[var(--color-phosphor)] text-[#04140A]" : "text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"}`}
              aria-pressed={sort === s}
            >
              {s}
            </button>
          ))}
        </div>

        <button
          onClick={() => setOrder(o => (o === "asc" ? "desc" : "asc"))}
          className="px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest border border-[var(--color-line-2)] rounded-[2px] text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
          aria-label="toggle sort direction"
        >
          {order}
        </button>

        {sources.length > 0 && (
          <select
            value={source}
            onChange={e => setSource(e.target.value)}
            className="px-2 py-1.5 font-mono text-[10px] uppercase tracking-widest border border-[var(--color-line-2)] rounded-[2px] bg-transparent text-[var(--color-muted)]"
            aria-label="filter by source"
          >
            <option value="">all sources</option>
            {sources.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
      </div>

      {error ? (
        <div className="panel-inset rounded-[2px] py-12 text-center font-mono text-xs text-[var(--color-red,#ff6b6b)]">
          failed to load catalog / {String((error as any)?.message || error)}
          <div className="mt-2 text-[var(--color-dim)]">check that the api is running on 7451 and that your api key is set</div>
        </div>
      ) : isLoading && !data ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="panel rounded-[2px] p-3 animate-pulse">
              <div className="flex items-start gap-3">
                <div className="w-12 h-12 rounded-[2px] panel-inset" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 bg-[var(--color-line)] rounded-[1px] w-3/4" />
                  <div className="h-2 bg-[var(--color-line)] rounded-[1px] w-1/2" />
                </div>
              </div>
              <div className="h-9 bg-[var(--color-line)] rounded-[1px] mt-3" />
            </div>
          ))}
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-dim)]">
          <Database size={32} weight="duotone" className="mx-auto mb-3 text-[var(--color-muted)]" />
          {debouncedQ || source
            ? <>no tracks match the current filter</>
            : <>index is empty / run reindex from <Link href="/library" className="text-[var(--color-phosphor)] hover:underline">index</Link></>
          }
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {data.items.map(t => (
              <Link
                key={t.id}
                href={`/track/${encodeURIComponent(t.id)}`}
                className="panel rounded-[2px] p-3 hover:border-[var(--color-phosphor-dim)] transition block"
              >
                <div className="flex items-start gap-3">
                  {t.artwork_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={t.artwork_url} alt="" className="w-12 h-12 rounded-[2px] object-cover border border-[var(--color-line)]" />
                  ) : (
                    <div className="w-12 h-12 rounded-[2px] panel-inset flex items-center justify-center font-mono text-[10px] text-[var(--color-dim)]">
                      <MusicNotes size={18} weight="duotone" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">{t.title || <span className="text-[var(--color-dim)]">untitled</span>}</div>
                    <div className="truncate font-mono text-[10px] text-[var(--color-muted)]">{t.artist || "unknown artist"}</div>
                    <div className="truncate font-mono text-[9px] text-[var(--color-dim)] mt-0.5">{t.id}</div>
                  </div>
                </div>
                <div className="mt-3 -mx-3 -mb-3">
                  <Spectrogram height={36} seed={t.id} />
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[10px]">
                  <Cell k="src" v={t.source || "—"} />
                  <Cell k="bpm" v={t.tempo_bpm ? t.tempo_bpm.toFixed(0) : "—"} />
                  <Cell k="dur" v={t.duration_s ? `${t.duration_s.toFixed(0)}s` : "—"} accent />
                </div>
              </Link>
            ))}
          </div>

          <div className="flex items-center justify-between mt-4 font-mono text-[10px] text-[var(--color-muted)]">
            <div>
              showing <span className="tabular-nums text-[var(--color-text)]">{showingFrom}</span>
              {" "}to <span className="tabular-nums text-[var(--color-text)]">{showingTo}</span>
              {" "}of <span className="tabular-nums text-[var(--color-phosphor)]">{total}</span>
            </div>
            <div className="flex gap-1">
              <button
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
                disabled={!canPrev}
                className="px-3 py-1.5 border border-[var(--color-line-2)] rounded-[2px] flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed hover:text-[var(--color-phosphor)]"
                aria-label="previous page"
              >
                <ArrowLeft size={12} weight="duotone" /> prev
              </button>
              <button
                onClick={() => setOffset(offset + PAGE)}
                disabled={!canNext}
                className="px-3 py-1.5 border border-[var(--color-line-2)] rounded-[2px] flex items-center gap-1 disabled:opacity-30 disabled:cursor-not-allowed hover:text-[var(--color-phosphor)]"
                aria-label="next page"
              >
                next <ArrowRight size={12} weight="duotone" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Cell({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="panel-inset px-2 py-1 rounded-[1px]">
      <div className="text-[9px] uppercase tracking-widest text-[var(--color-dim)]">{k}</div>
      <div className={`tabular-nums truncate ${accent ? "text-[var(--color-phosphor)]" : "text-[var(--color-text)]"}`}>{v}</div>
    </div>
  );
}
