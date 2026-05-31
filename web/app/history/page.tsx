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
}

interface HistoryList {
  items: HistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE = 25;

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
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<HistoryList | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Inline-edit state
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [tagging, setTagging] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");

  const fetchHistory = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const u = new URL(window.location.origin + "/api/history");
      u.searchParams.set("limit", String(PAGE));
      u.searchParams.set("offset", String(offset));
      if (q.trim()) u.searchParams.set("q", q.trim());
      if (tag.trim()) u.searchParams.set("tag", tag.trim().toLowerCase());
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
  }, [q, tag, offset]);

  // Debounced refetch on filter change
  useEffect(() => {
    const t = setTimeout(() => { void fetchHistory(); }, 200);
    return () => clearTimeout(t);
  }, [fetchHistory]);

  // Reset paging on filter change
  useEffect(() => { setOffset(0); }, [q, tag]);

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
          <ExportAllMenu q={q} tag={tag} total={total} />
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
        <div className="panel rounded-[2px] divide-y divide-[var(--color-line)]">
          {data.items.map((it) => {
            const top = it.results[0];
            return (
              <div key={it.id} className="p-3 flex flex-col md:flex-row md:items-center gap-3">
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

function ExportAllMenu({ q, tag, total }: { q: string; tag: string; total: number }) {
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
