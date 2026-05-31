"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Spectrogram from "@/components/Spectrogram";
import {
  allTags,
  clearMatches,
  deleteMatch,
  filterMatches,
  loadMatches,
  normaliseTags,
  sortMatches,
  updateMatch,
  type MatchSort,
  type StoredMatch,
} from "@/lib/history";
import ExportMenu from "@/components/ExportMenu";
import {
  CaretLeft,
  CaretRight,
  PencilSimple,
  Tag,
  Trash,
  Check,
  X,
} from "@phosphor-icons/react/dist/ssr";

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

const PAGE_SIZE = 25;
const RANGE_PRESETS: { id: string; label: string; ms: number }[] = [
  { id: "all", label: "all time", ms: 0 },
  { id: "24h", label: "last 24h", ms: 24 * 3600_000 },
  { id: "7d", label: "last 7d", ms: 7 * 86_400_000 },
  { id: "30d", label: "last 30d", ms: 30 * 86_400_000 },
];

export default function MatchesPage() {
  const [items, setItems] = useState<StoredMatch[] | null>(null);
  const [q, setQ] = useState("");
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [minScore, setMinScore] = useState(0);
  const [range, setRange] = useState("all");
  const [sort, setSort] = useState<MatchSort>("ts_desc");
  const [page, setPage] = useState(1);

  // Inline-edit state
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [tagging, setTagging] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");

  useEffect(() => { setItems(loadMatches()); }, []);
  useEffect(() => { setPage(1); }, [q, activeTags, minScore, range, sort]);

  const tagOptions = useMemo(() => (items ? allTags(items) : []), [items]);

  const filtered = useMemo(() => {
    if (!items) return [];
    const preset = RANGE_PRESETS.find(r => r.id === range);
    const since = preset && preset.ms > 0 ? Date.now() - preset.ms : undefined;
    return sortMatches(
      filterMatches(items, { q, tags: activeTags, minScore, since }),
      sort,
    );
  }, [items, q, activeTags, minScore, range, sort]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);
  const start = (pageClamped - 1) * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);

  function refresh() { setItems(loadMatches()); }

  function toggleTag(t: string) {
    setActiveTags(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);
  }
  function startRename(m: StoredMatch) {
    setRenaming(m.query_id); setRenameDraft(m.name ?? "");
    setTagging(null);
  }
  function commitRename() {
    if (!renaming) return;
    updateMatch(renaming, { name: renameDraft });
    setRenaming(null); setRenameDraft(""); refresh();
  }
  function startTagging(m: StoredMatch) {
    setTagging(m.query_id); setTagDraft((m.tags ?? []).join(", "));
    setRenaming(null);
  }
  function commitTags() {
    if (!tagging) return;
    const tags = normaliseTags(tagDraft.split(/[,\s]+/));
    updateMatch(tagging, { tags });
    setTagging(null); setTagDraft(""); refresh();
  }
  function removeOne(id: string) {
    if (!confirm("delete this query from your local log?")) return;
    deleteMatch(id); refresh();
  }

  if (items === null) {
    return (
      <div className="px-4 py-8 font-mono text-xs text-[var(--color-muted)]">
        loading query log...
      </div>
    );
  }

  const filtersActive = !!q || activeTags.length > 0 || minScore > 0 || range !== "all";

  return (
    <div className="px-4 py-4">
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 mb-3">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            query log{" "}
            <span className="text-[var(--color-text)]">
              / {filtered.length}{filtered.length !== items.length ? ` of ${items.length}` : ""}
            </span>
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] mt-1">
            local. queries live in this browser. rename, tag, search, paginate. clear to reset.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            placeholder="search / id / name / track / artist / tag"
            value={q}
            onChange={e => setQ(e.target.value)}
            className="w-72 max-w-full"
            aria-label="search query log"
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

      {/* Filter row */}
      <div className="panel-inset rounded-[2px] px-3 py-2 mb-3 flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">range</span>
            {RANGE_PRESETS.map(r => (
              <button
                key={r.id}
                onClick={() => setRange(r.id)}
                className={`px-2 py-1 rounded-[2px] font-mono text-[10px] uppercase tracking-widest border ${
                  range === r.id
                    ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)]"
                    : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
              min score {minScore.toFixed(2)}
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={minScore}
              onChange={e => setMinScore(parseFloat(e.target.value))}
              className="w-40"
              aria-label="minimum best-candidate score"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">sort</span>
            <select
              value={sort}
              onChange={e => setSort(e.target.value as MatchSort)}
              className="font-mono text-[11px] bg-transparent border border-[var(--color-line)] rounded-[2px] px-2 py-1"
              aria-label="sort order"
            >
              <option value="ts_desc">newest</option>
              <option value="ts_asc">oldest</option>
              <option value="score_desc">score, high to low</option>
              <option value="score_asc">score, low to high</option>
              <option value="latency_asc">latency, fast to slow</option>
              <option value="latency_desc">latency, slow to fast</option>
            </select>
          </div>
          {filtersActive && (
            <button
              onClick={() => { setQ(""); setActiveTags([]); setMinScore(0); setRange("all"); }}
              className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-amber)] inline-flex items-center gap-1"
            >
              <X size={11} weight="duotone" /> reset filters
            </button>
          )}
        </div>
        {tagOptions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)] mr-1">tags</span>
            {tagOptions.map(t => {
              const on = activeTags.includes(t.tag);
              return (
                <button
                  key={t.tag}
                  onClick={() => toggleTag(t.tag)}
                  className={`px-2 py-0.5 rounded-[2px] font-mono text-[10px] uppercase tracking-widest border ${
                    on
                      ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)] bg-[color-mix(in_srgb,var(--color-phosphor)_12%,transparent)]"
                      : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-text)]"
                  }`}
                >
                  #{t.tag}
                  <span className="ml-1 text-[var(--color-dim)] tabular-nums">{t.count}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {items.length === 0 ? (
        <div className="panel-inset rounded-[2px] py-16 text-center font-mono text-xs text-[var(--color-dim)]">
          empty / run a capture on the landing page to populate
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel-inset rounded-[2px] py-12 text-center font-mono text-xs text-[var(--color-dim)]">
          no rows match the current filters
        </div>
      ) : (
        <div className="panel rounded-[2px] overflow-hidden">
          <table className="dense-table">
            <thead>
              <tr>
                <th className="w-[160px]">query</th>
                <th className="w-[140px]">ts</th>
                <th>best match / tags</th>
                <th className="w-[180px]">artist</th>
                <th className="w-[220px]">query fingerprint</th>
                <th className="w-[80px] text-right">score</th>
                <th className="w-[70px] text-right">cand</th>
                <th className="w-[90px] text-right">latency</th>
                <th className="w-[110px] text-right">actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(m => {
                const best = m.results[0];
                const isRenaming = renaming === m.query_id;
                const isTagging = tagging === m.query_id;
                return (
                  <tr key={m.query_id}>
                    <td className="font-mono text-[var(--color-phosphor)]">
                      {isRenaming ? (
                        <div className="flex items-center gap-1">
                          <input
                            value={renameDraft}
                            onChange={e => setRenameDraft(e.target.value)}
                            onKeyDown={e => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenaming(null); }}
                            placeholder="name this query"
                            className="w-full text-[11px]"
                            autoFocus
                          />
                          <button onClick={commitRename} className="text-[var(--color-phosphor)] hover:opacity-80" aria-label="save name">
                            <Check size={14} weight="duotone" />
                          </button>
                          <button onClick={() => setRenaming(null)} className="text-[var(--color-muted)] hover:text-[var(--color-amber)]" aria-label="cancel rename">
                            <X size={14} weight="duotone" />
                          </button>
                        </div>
                      ) : (
                        <Link href={`/matches/${m.query_id}`} className="hover:underline block truncate" title={m.query_id}>
                          {m.name || m.query_id.slice(0, 8)}
                        </Link>
                      )}
                      {m.filename && !isRenaming && (
                        <div className="font-mono text-[10px] text-[var(--color-dim)] truncate" title={m.filename}>
                          {m.filename}
                        </div>
                      )}
                    </td>
                    <td className="font-mono text-[var(--color-muted)] tabular-nums" title={fmtTs(m.ts)}>
                      {fmtRelative(m.ts)}
                    </td>
                    <td>
                      <Link href={`/matches/${m.query_id}`} className="hover:text-[var(--color-phosphor)]">
                        {best ? (best.title || <span className="text-[var(--color-dim)]">untitled</span>) : <span className="text-[var(--color-dim)]">no candidate</span>}
                      </Link>
                      {isTagging ? (
                        <div className="flex items-center gap-1 mt-1">
                          <input
                            value={tagDraft}
                            onChange={e => setTagDraft(e.target.value)}
                            onKeyDown={e => { if (e.key === "Enter") commitTags(); if (e.key === "Escape") setTagging(null); }}
                            placeholder="comma or space separated"
                            className="w-full text-[11px]"
                            autoFocus
                          />
                          <button onClick={commitTags} className="text-[var(--color-phosphor)] hover:opacity-80" aria-label="save tags">
                            <Check size={14} weight="duotone" />
                          </button>
                          <button onClick={() => setTagging(null)} className="text-[var(--color-muted)] hover:text-[var(--color-amber)]" aria-label="cancel tag edit">
                            <X size={14} weight="duotone" />
                          </button>
                        </div>
                      ) : m.tags && m.tags.length > 0 ? (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {m.tags.map(t => (
                            <button
                              key={t}
                              onClick={() => toggleTag(t)}
                              className="px-1.5 py-0 rounded-[2px] font-mono text-[9px] uppercase tracking-widest border border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)]"
                            >
                              #{t}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </td>
                    <td className="text-[var(--color-muted)] truncate" title={best?.artist || ""}>
                      {best?.artist || <span className="text-[var(--color-dim)]">—</span>}
                    </td>
                    <td><Spectrogram height={28} seed={m.query_id} /></td>
                    <td className="font-mono text-right tabular-nums">
                      {best ? (
                        <span className={best.score >= 0.5 ? "text-[var(--color-phosphor)]" : best.score >= 0.3 ? "text-[var(--color-amber)]" : "text-[var(--color-muted)]"}>
                          {best.score.toFixed(4)}
                        </span>
                      ) : <span className="text-[var(--color-dim)]">—</span>}
                    </td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-muted)]">{m.count}</td>
                    <td className="font-mono text-right tabular-nums text-[var(--color-phosphor)]">
                      {m.elapsed_ms}<span className="text-[var(--color-dim)] ml-0.5">ms</span>
                    </td>
                    <td className="text-right">
                      <div className="inline-flex items-center gap-1">
                        <button
                          onClick={() => startRename(m)}
                          title="rename"
                          aria-label="rename query"
                          className="p-1 text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
                        >
                          <PencilSimple size={14} weight="duotone" />
                        </button>
                        <button
                          onClick={() => startTagging(m)}
                          title="edit tags"
                          aria-label="edit tags"
                          className="p-1 text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
                        >
                          <Tag size={14} weight="duotone" />
                        </button>
                        <button
                          onClick={() => removeOne(m.query_id)}
                          title="delete"
                          aria-label="delete query"
                          className="p-1 text-[var(--color-muted)] hover:text-[var(--color-amber)]"
                        >
                          <Trash size={14} weight="duotone" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Pagination */}
          <div className="flex items-center justify-between px-3 py-2 border-t border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
            <span>
              showing {start + 1} to {Math.min(start + PAGE_SIZE, filtered.length)} of {filtered.length}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={pageClamped <= 1}
                className="p-1 hover:text-[var(--color-phosphor)] disabled:opacity-30 disabled:cursor-not-allowed"
                aria-label="previous page"
              >
                <CaretLeft size={12} weight="duotone" />
              </button>
              <span className="px-2 text-[var(--color-text)] tabular-nums">
                {pageClamped} / {totalPages}
              </span>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={pageClamped >= totalPages}
                className="p-1 hover:text-[var(--color-phosphor)] disabled:opacity-30 disabled:cursor-not-allowed"
                aria-label="next page"
              >
                <CaretRight size={12} weight="duotone" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
