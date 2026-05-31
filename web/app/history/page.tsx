"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  CloudArrowUp,
  MagnifyingGlass,
  PencilSimple,
  Tag,
  Trash,
  ArrowsClockwise,
  Key,
  Check,
  X,
  DownloadSimple,
  FileCsv,
  FileCode,
  CheckSquare,
  Square,
  Stack,
  Star,
  SortAscending,
} from "@phosphor-icons/react/dist/ssr";
import { useApiKey } from "@/lib/apiKey";
import { historyToShareInput } from "@/lib/share";
import ShareButton from "@/components/ShareButton";

interface MatchResult {
  track_id: string;
  title: string;
  artist?: string;
  album?: string;
  score: number;
  segment_index?: number;
  preview_url?: string | null;
  artwork_url?: string | null;
  source?: string;
  tempo_bpm?: number | null;
}

interface HistoryItem {
  id: string;
  created_at: number;
  updated_at: number;
  query_id: string;
  elapsed_ms: number;
  count: number;
  results: MatchResult[];
  filename: string | null;
  duration_sec: number | null;
  name: string | null;
  tags: string[];
  starred: boolean;
}

interface HistoryList {
  items: HistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE = 25;

type SortKey = "recent" | "oldest" | "name" | "results" | "top_score";
const SORT_LABELS: Record<SortKey, string> = {
  recent: "newest first",
  oldest: "oldest first",
  name: "name a / z",
  results: "most matches",
  top_score: "best score",
};

function fmtTs(epochSec: number): string {
  if (!epochSec) return "";
  const d = new Date(epochSec * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtRel(epochSec: number): string {
  const ms = Date.now() - epochSec * 1000;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}

export default function HistoryPage() {
  const [apiKey] = useApiKey();
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");
  const [starredOnly, setStarredOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<HistoryList | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Inline-edit state
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [tagging, setTagging] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");

  // Bulk-select state
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<"delete" | "tag" | null>(null);
  const [bulkTagDraft, setBulkTagDraft] = useState("");
  const [showBulkTag, setShowBulkTag] = useState(false);

  const fetchHistory = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const u = new URL(window.location.origin + "/api/history");
      u.searchParams.set("limit", String(PAGE));
      u.searchParams.set("offset", String(offset));
      if (q.trim()) u.searchParams.set("q", q.trim());
      if (tag.trim()) u.searchParams.set("tag", tag.trim().toLowerCase());
      if (sort !== "recent") u.searchParams.set("sort", sort);
      if (starredOnly) u.searchParams.set("starred", "true");
      const r = await fetch(u.pathname + u.search);
      if (r.status === 401) {
        setErr("missing api key. set one in settings to enable cloud history.");
        setData(null);
        return;
      }
      if (!r.ok) {
        setErr(`fetch failed (${r.status})`);
        setData(null);
        return;
      }
      setData((await r.json()) as HistoryList);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [q, tag, offset, sort, starredOnly]);

  // Debounced refetch on filter change
  useEffect(() => {
    const t = setTimeout(() => { void fetchHistory(); }, 200);
    return () => clearTimeout(t);
  }, [fetchHistory]);

  // Reset paging on filter change
  useEffect(() => { setOffset(0); }, [q, tag, sort, starredOnly]);

  // Drop stale selections when the visible list changes
  useEffect(() => {
    if (!data) return;
    const visible = new Set(data.items.map((i) => i.id));
    setSelected((prev) => {
      const next = new Set<string>();
      for (const id of prev) if (visible.has(id)) next.add(id);
      return next.size === prev.size ? prev : next;
    });
  }, [data]);

  const visibleIds = useMemo(() => data?.items.map((i) => i.id) ?? [], [data]);
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const someSelected = selected.size > 0 && !allSelected;

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelected((prev) => {
      if (visibleIds.every((id) => prev.has(id))) {
        const next = new Set(prev);
        for (const id of visibleIds) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of visibleIds) next.add(id);
      return next;
    });
  }
  function clearSelection() { setSelected(new Set()); }

  async function bulkDelete() {
    if (selected.size === 0) return;
    if (!confirm(`delete ${selected.size} ${selected.size === 1 ? "entry" : "entries"}? this cannot be undone.`)) return;
    setBulkBusy("delete");
    try {
      const ids = Array.from(selected);
      // Cap parallelism so we don't hammer the backend.
      const limit = 6;
      let i = 0;
      let failed = 0;
      async function worker() {
        while (i < ids.length) {
          const id = ids[i++];
          try {
            const r = await fetch(`/api/history/${encodeURIComponent(id)}`, { method: "DELETE" });
            if (!r.ok) failed++;
          } catch { failed++; }
        }
      }
      await Promise.all(Array.from({ length: Math.min(limit, ids.length) }, worker));
      clearSelection();
      await fetchHistory();
      if (failed > 0) setErr(`bulk delete: ${failed} of ${ids.length} failed`);
    } finally {
      setBulkBusy(null);
    }
  }

  async function bulkAddTags() {
    if (selected.size === 0) return;
    const tags = bulkTagDraft.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (tags.length === 0) return;
    setBulkBusy("tag");
    try {
      const items = data?.items.filter((it) => selected.has(it.id)) ?? [];
      const limit = 6;
      let i = 0;
      let failed = 0;
      async function worker() {
        while (i < items.length) {
          const it = items[i++];
          const merged = Array.from(new Set([...(it.tags || []), ...tags]));
          try {
            const r = await fetch(`/api/history/${encodeURIComponent(it.id)}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ tags: merged }),
            });
            if (!r.ok) failed++;
          } catch { failed++; }
        }
      }
      await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
      setBulkTagDraft("");
      setShowBulkTag(false);
      await fetchHistory();
      if (failed > 0) setErr(`bulk tag: ${failed} of ${items.length} failed`);
    } finally {
      setBulkBusy(null);
    }
  }

  async function patch(id: string, body: Record<string, unknown>) {
    const r = await fetch(`/api/history/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) void fetchHistory();
  }

  async function remove(id: string) {
    if (!confirm("delete this entry? this cannot be undone.")) return;
    const r = await fetch(`/api/history/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (r.ok) void fetchHistory();
  }

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  const tagOptions = useMemo(() => {
    if (!data) return [] as string[];
    const set = new Set<string>();
    for (const it of data.items) for (const t of it.tags) set.add(t);
    return Array.from(set).sort();
  }, [data]);

  return (
    <div className="px-4 py-4 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            history / cloud, synced per account
          </h1>
          <p className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest mt-1">
            every match saves to your tenant. survives device switches and storage wipes.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {apiKey ? (
            <span className="font-mono text-[10px] text-[var(--color-phosphor)] uppercase tracking-widest flex items-center gap-1">
              <CloudArrowUp size={14} weight="duotone" /> sync on
            </span>
          ) : (
            <Link href="/settings" className="font-mono text-[10px] text-[var(--color-amber)] uppercase tracking-widest flex items-center gap-1 underline">
              <Key size={14} weight="duotone" /> set api key
            </Link>
          )}
          <button
            onClick={() => fetchHistory()}
            className="font-mono text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] flex items-center gap-1"
            disabled={loading}
          >
            <ArrowsClockwise size={12} weight="duotone" /> refresh
          </button>
          <ExportAllMenu q={q} tag={tag} starredOnly={starredOnly} total={total} />
        </div>
      </div>

      {/* Filters */}
      <div className="panel rounded-[2px] p-3 flex flex-wrap gap-2 items-center">
        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
          <MagnifyingGlass size={14} weight="duotone" className="text-[var(--color-dim)]" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="search name, file, artist, title"
            className="bg-transparent outline-none flex-1 font-mono text-[12px] placeholder:text-[var(--color-dim)]"
          />
        </div>
        <select
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          className="bg-[var(--color-bg)] border border-[var(--color-line)] font-mono text-[11px] uppercase tracking-widest px-2 py-1"
        >
          <option value="">all tags</option>
          {tagOptions.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <div className="flex items-center gap-1">
          <SortAscending size={12} weight="duotone" className="text-[var(--color-dim)]" />
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="sort history"
            className="bg-[var(--color-bg)] border border-[var(--color-line)] font-mono text-[11px] uppercase tracking-widest px-2 py-1"
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
              <option key={k} value={k}>{SORT_LABELS[k]}</option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => setStarredOnly((v) => !v)}
          aria-pressed={starredOnly}
          title={starredOnly ? "showing starred only" : "show only starred"}
          className={`font-mono text-[10px] uppercase tracking-widest px-2 py-1 border flex items-center gap-1 ${starredOnly ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)] bg-[rgba(29,185,84,0.06)]" : "border-[var(--color-line)] text-[var(--color-muted)] hover:bg-[var(--color-panel)]"}`}
        >
          <Star size={12} weight={starredOnly ? "fill" : "duotone"} /> starred
        </button>
        <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest tabular-nums">
          {loading ? "loading..." : `${total} entries`}
        </span>
      </div>

      {err && (
        <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] px-3 py-2 text-[var(--color-amber)] font-mono text-xs">
          {err}
        </div>
      )}

      {/* Table */}
      {loading && !data && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-14 panel rounded-[2px] animate-pulse" />
          ))}
        </div>
      )}

      {!loading && data && data.items.length === 0 && !err && (
        <div className="panel rounded-[2px] p-8 text-center font-mono text-[11px] uppercase tracking-widest text-[var(--color-dim)]">
          no cloud history yet. record a hum from <Link className="text-[var(--color-phosphor)] underline" href="/">capture</Link> or try the <Link className="text-[var(--color-phosphor)] underline" href="/demo">demo</Link> while signed in to see entries appear here.
        </div>
      )}

      {data && data.items.length > 0 && (
        <>
        {/* Bulk action bar */}
        <div className="panel rounded-[2px] p-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={toggleAll}
            aria-label={allSelected ? "deselect all on page" : "select all on page"}
            title={allSelected ? "deselect all on page" : "select all on page"}
            className="p-1 text-[var(--color-muted)] hover:text-[var(--color-text)]"
          >
            {allSelected ? <CheckSquare size={16} weight="duotone" /> : someSelected ? <Stack size={16} weight="duotone" /> : <Square size={16} weight="duotone" />}
          </button>
          <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)] tabular-nums">
            {selected.size === 0 ? "none selected" : `${selected.size} selected`}
          </span>
          {selected.size > 0 && (
            <>
              <button
                onClick={clearSelection}
                className="font-mono text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] text-[var(--color-muted)]"
              >
                clear
              </button>
              <button
                onClick={() => setShowBulkTag((v) => !v)}
                disabled={bulkBusy !== null}
                className="font-mono text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] flex items-center gap-1 disabled:opacity-40"
              >
                <Tag size={12} weight="duotone" /> tag
              </button>
              <button
                onClick={() => void bulkDelete()}
                disabled={bulkBusy !== null}
                className="font-mono text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-amber)] text-[var(--color-amber)] hover:bg-[rgba(245,158,11,0.06)] flex items-center gap-1 disabled:opacity-40"
              >
                <Trash size={12} weight="duotone" /> {bulkBusy === "delete" ? "deleting..." : "delete"}
              </button>
            </>
          )}
          {showBulkTag && selected.size > 0 && (
            <div className="flex items-center gap-1 w-full md:w-auto">
              <input
                autoFocus
                value={bulkTagDraft}
                onChange={(e) => setBulkTagDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void bulkAddTags();
                  if (e.key === "Escape") { setShowBulkTag(false); setBulkTagDraft(""); }
                }}
                placeholder="tags, comma separated"
                className="bg-[var(--color-bg)] border border-[var(--color-line)] font-mono text-[11px] px-2 py-1 flex-1 min-w-[160px]"
              />
              <button
                onClick={() => void bulkAddTags()}
                disabled={bulkBusy !== null || bulkTagDraft.trim() === ""}
                className="p-1 text-[var(--color-phosphor)] disabled:opacity-40"
                aria-label="apply tags"
              >
                <Check size={14} />
              </button>
              <button
                onClick={() => { setShowBulkTag(false); setBulkTagDraft(""); }}
                className="p-1 text-[var(--color-dim)]"
                aria-label="cancel"
              >
                <X size={14} />
              </button>
            </div>
          )}
        </div>
        <div className="panel rounded-[2px] divide-y divide-[var(--color-line)]">
          {data.items.map((it) => {
            const top = it.results[0];
            const isSel = selected.has(it.id);
            return (
              <div key={it.id} className={`p-3 flex flex-col md:flex-row md:items-center gap-3 ${isSel ? "bg-[rgba(29,185,84,0.04)]" : ""}`}>
                <button
                  type="button"
                  onClick={() => toggleOne(it.id)}
                  aria-label={isSel ? "deselect entry" : "select entry"}
                  aria-pressed={isSel}
                  className="p-1 text-[var(--color-muted)] hover:text-[var(--color-text)] shrink-0 self-start md:self-center"
                >
                  {isSel ? <CheckSquare size={16} weight="duotone" className="text-[var(--color-phosphor)]" /> : <Square size={16} weight="duotone" />}
                </button>
                <div className="flex-1 min-w-0">
                  {renaming === it.id ? (
                    <div className="flex items-center gap-1">
                      <input
                        autoFocus
                        value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { patch(it.id, { name: renameDraft }); setRenaming(null); }
                          if (e.key === "Escape") setRenaming(null);
                        }}
                        className="bg-[var(--color-bg)] border border-[var(--color-line)] font-mono text-[12px] px-2 py-0.5 flex-1"
                      />
                      <button onClick={() => { patch(it.id, { name: renameDraft }); setRenaming(null); }} className="p-1 text-[var(--color-phosphor)]"><Check size={14} /></button>
                      <button onClick={() => setRenaming(null)} className="p-1 text-[var(--color-dim)]"><X size={14} /></button>
                    </div>
                  ) : (
                    <div className="font-mono text-[13px] truncate">
                      {it.name || it.filename || `query ${it.query_id.slice(0, 8)}`}
                    </div>
                  )}
                  <div className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest mt-0.5 flex flex-wrap gap-x-3 gap-y-1">
                    <span title={fmtTs(it.created_at)}>{fmtRel(it.created_at)}</span>
                    <span>{it.count} matches</span>
                    <span className="tabular-nums">{it.elapsed_ms} ms</span>
                    {top && (
                      <span className="text-[var(--color-text)] truncate max-w-[260px]">
                        top: {top.title}{top.artist ? ` / ${top.artist}` : ""} ({top.score.toFixed(2)})
                      </span>
                    )}
                  </div>
                  {(it.tags.length > 0 || tagging === it.id) && (
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {it.tags.map((t) => (
                        <span key={t} className="font-mono text-[9px] uppercase tracking-widest px-1.5 py-0.5 border border-[var(--color-line)] text-[var(--color-muted)]">{t}</span>
                      ))}
                      {tagging === it.id && (
                        <input
                          autoFocus
                          value={tagDraft}
                          onChange={(e) => setTagDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              const tags = tagDraft.split(",").map((s) => s.trim()).filter(Boolean);
                              patch(it.id, { tags: Array.from(new Set([...it.tags, ...tags])) });
                              setTagging(null);
                              setTagDraft("");
                            }
                            if (e.key === "Escape") { setTagging(null); setTagDraft(""); }
                          }}
                          placeholder="add tags, comma separated"
                          className="bg-[var(--color-bg)] border border-[var(--color-line)] font-mono text-[10px] px-1.5 py-0.5 flex-1 min-w-[140px]"
                        />
                      )}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    title={it.starred ? "unstar" : "star"}
                    aria-label={it.starred ? "unstar entry" : "star entry"}
                    aria-pressed={it.starred}
                    onClick={() => patch(it.id, { starred: !it.starred })}
                    className={`p-1.5 border border-[var(--color-line)] hover:bg-[var(--color-panel)] ${it.starred ? "text-[var(--color-phosphor)]" : "text-[var(--color-muted)]"}`}
                  >
                    <Star size={13} weight={it.starred ? "fill" : "duotone"} />
                  </button>
                  <button
                    title="rename"
                    onClick={() => { setRenaming(it.id); setRenameDraft(it.name || ""); }}
                    className="p-1.5 border border-[var(--color-line)] hover:bg-[var(--color-panel)] text-[var(--color-muted)]"
                  >
                    <PencilSimple size={13} weight="duotone" />
                  </button>
                  <button
                    title="add tag"
                    onClick={() => { setTagging(it.id); setTagDraft(""); }}
                    className="p-1.5 border border-[var(--color-line)] hover:bg-[var(--color-panel)] text-[var(--color-muted)]"
                  >
                    <Tag size={13} weight="duotone" />
                  </button>
                  <ShareButton
                    compact
                    input={historyToShareInput(it, it.name ?? null)}
                  />
                  <button
                    title="delete"
                    onClick={() => remove(it.id)}
                    className="p-1.5 border border-[var(--color-line)] hover:bg-[var(--color-panel)] text-[var(--color-amber)]"
                  >
                    <Trash size={13} weight="duotone" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        </>
      )}

      {/* Pagination */}
      {data && total > PAGE && (
        <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
          <span>page {page} of {pages}</span>
          <div className="flex gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
              className="px-2 py-1 border border-[var(--color-line)] disabled:opacity-40"
            >
              prev
            </button>
            <button
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
              className="px-2 py-1 border border-[var(--color-line)] disabled:opacity-40"
            >
              next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ExportAllMenu({ q, tag, starredOnly, total }: { q: string; tag: string; starredOnly: boolean; total: number }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"csv" | "json" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const disabled = total === 0;

  async function download(format: "csv" | "json") {
    setErr(null);
    setBusy(format);
    try {
      const u = new URL("/api/history/export", window.location.origin);
      u.searchParams.set("format", format);
      if (q.trim()) u.searchParams.set("q", q.trim());
      if (tag.trim()) u.searchParams.set("tag", tag.trim().toLowerCase());
      if (starredOnly) u.searchParams.set("starred", "true");
      const r = await fetch(u.pathname + u.search);
      if (!r.ok) {
        if (r.status === 401) throw new Error("set an api key in settings first");
        throw new Error(`export failed (${r.status})`);
      }
      const blob = await r.blob();
      const link = document.createElement("a");
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      link.href = URL.createObjectURL(blob);
      link.download = `clawhum-history-${stamp}.${format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "export failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        title={disabled ? "no history to export" : "export all matching history"}
        className="font-mono text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <DownloadSimple size={12} weight="duotone" /> export
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-1 z-10 panel border border-[var(--color-line)] rounded-[2px] min-w-[180px] bg-[var(--color-bg)]"
        >
          <button
            role="menuitem"
            onClick={() => download("csv")}
            disabled={busy !== null}
            className="w-full text-left px-3 py-2 font-mono text-[11px] uppercase tracking-widest hover:bg-[var(--color-panel)] flex items-center gap-2 disabled:opacity-40"
          >
            <FileCsv size={14} weight="duotone" /> {busy === "csv" ? "downloading..." : "csv (flat rows)"}
          </button>
          <button
            role="menuitem"
            onClick={() => download("json")}
            disabled={busy !== null}
            className="w-full text-left px-3 py-2 font-mono text-[11px] uppercase tracking-widest hover:bg-[var(--color-panel)] flex items-center gap-2 border-t border-[var(--color-line)] disabled:opacity-40"
          >
            <FileCode size={14} weight="duotone" /> {busy === "json" ? "downloading..." : "json (nested)"}
          </button>
          {err && (
            <div className="px-3 py-2 border-t border-[var(--color-line)] text-[var(--color-amber)] font-mono text-[10px]">
              {err}
            </div>
          )}
          <div className="px-3 py-2 border-t border-[var(--color-line)] font-mono text-[9px] text-[var(--color-dim)] uppercase tracking-widest">
            {total} entries match filters
          </div>
        </div>
      )}
    </div>
  );
}
