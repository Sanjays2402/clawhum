"use client";

/**
 * Collections page. Owners create named, ordered bundles of result
 * snapshots ("collections") and publish them at /c/<id>. The page pulls
 * the user's cloud history and lets them pick rows to drop into a
 * builder, set a title plus optional note, then save. All mutations go
 * through /api/collections. Loading, error, and empty states are
 * explicit; the editor stays usable without an API key (it just cannot
 * save until one is configured on /settings).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Stack,
  Plus,
  Trash,
  Copy,
  Check,
  ArrowsClockwise,
  ArrowSquareOut,
  Key,
  X,
  FloppyDisk,
  PencilSimple,
  CaretUp,
  CaretDown,
} from "@phosphor-icons/react/dist/ssr";
import { useApiKey, getApiKey } from "@/lib/apiKey";
import { toast } from "@/lib/toast";

interface CollectionSummary {
  id: string;
  created_at: number;
  updated_at: number;
  title: string;
  note: string | null;
  item_count: number;
  url_path: string;
}

interface MatchResult {
  track_id: string;
  title: string;
  artist?: string;
  album?: string;
  score: number;
  segment_index?: number;
}

interface CollectionItem {
  label: string;
  results: MatchResult[];
  query_id?: string | null;
  elapsed_ms?: number;
  filename?: string | null;
  duration_sec?: number | null;
}

interface CollectionDetail {
  id: string;
  created_at: number;
  updated_at: number;
  title: string;
  note: string | null;
  items: CollectionItem[];
}

interface HistoryItem {
  id: string;
  created_at: number;
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
  history: HistoryItem[];
  total: number;
}

const MAX_ITEMS = 50;
const MAX_TITLE = 80;
const MAX_NOTE = 280;

function fmtRel(epochSec: number): string {
  if (!epochSec) return "";
  const ms = Date.now() - epochSec * 1000;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  return h;
}

function historyToItem(h: HistoryItem): CollectionItem {
  return {
    label: h.name || h.filename || h.query_id || "untitled",
    results: h.results.slice(0, 5),
    query_id: h.query_id,
    elapsed_ms: h.elapsed_ms,
    filename: h.filename,
    duration_sec: h.duration_sec,
  };
}

export default function CollectionsPage() {
  const [apiKey] = useApiKey();
  const [list, setList] = useState<CollectionSummary[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [items, setItems] = useState<CollectionItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [historyErr, setHistoryErr] = useState<string | null>(null);

  const fetchList = useCallback(async () => {
    setListLoading(true);
    setListErr(null);
    try {
      const r = await fetch("/api/collections", { headers: authHeaders(), cache: "no-store" });
      if (r.status === 401) {
        setList(null);
        setListErr("api key required. set one on the settings page.");
        return;
      }
      if (!r.ok) {
        const body = await r.text();
        setListErr(body.slice(0, 240) || `request failed (${r.status})`);
        setList(null);
        return;
      }
      const data = await r.json();
      setList(data.collections || []);
    } catch (e) {
      setListErr(e instanceof Error ? e.message : String(e));
    } finally {
      setListLoading(false);
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    setHistoryErr(null);
    try {
      const r = await fetch("/api/history?limit=30&sort=recent", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (r.status === 401) {
        setHistoryErr("api key required to load history.");
        setHistory(null);
        return;
      }
      if (!r.ok) {
        setHistoryErr(`history request failed (${r.status})`);
        return;
      }
      const data: HistoryList = await r.json();
      setHistory(data.history || []);
    } catch (e) {
      setHistoryErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    fetchList();
    fetchHistory();
  }, [fetchList, fetchHistory, apiKey]);

  const resetEditor = useCallback(() => {
    setEditingId(null);
    setTitle("");
    setNote("");
    setItems([]);
    setSaveErr(null);
  }, []);

  const loadForEdit = useCallback(async (cid: string) => {
    setSaveErr(null);
    try {
      const r = await fetch(`/api/collections/${encodeURIComponent(cid)}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`could not load ${cid}`);
      const data: CollectionDetail = await r.json();
      setEditingId(data.id);
      setTitle(data.title);
      setNote(data.note || "");
      setItems(data.items || []);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "load failed");
    }
  }, []);

  const addFromHistory = useCallback((h: HistoryItem) => {
    setItems((prev) => {
      if (prev.length >= MAX_ITEMS) {
        toast.error(`max ${MAX_ITEMS} items per collection`);
        return prev;
      }
      return [...prev, historyToItem(h)];
    });
  }, []);

  const removeItem = useCallback((idx: number) => {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const moveItem = useCallback((idx: number, dir: -1 | 1) => {
    setItems((prev) => {
      const next = prev.slice();
      const j = idx + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  }, []);

  const renameItem = useCallback((idx: number, label: string) => {
    setItems((prev) => prev.map((it, i) => (i === idx ? { ...it, label } : it)));
  }, []);

  const save = useCallback(async () => {
    setSaveErr(null);
    const trimmed = title.trim();
    if (!trimmed) { setSaveErr("title is required"); return; }
    if (trimmed.length > MAX_TITLE) { setSaveErr(`title too long (max ${MAX_TITLE})`); return; }
    if (note.length > MAX_NOTE) { setSaveErr(`note too long (max ${MAX_NOTE})`); return; }
    if (items.length === 0) { setSaveErr("add at least one item from your history"); return; }
    setSaving(true);
    try {
      const body = JSON.stringify({ title: trimmed, note: note.trim() || null, items });
      const url = editingId
        ? `/api/collections/${encodeURIComponent(editingId)}`
        : "/api/collections";
      const method = editingId ? "PATCH" : "POST";
      const r = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body,
      });
      if (r.status === 401) {
        setSaveErr("api key required. set one on the settings page.");
        return;
      }
      if (!r.ok) {
        const t = await r.text();
        setSaveErr(t.slice(0, 240) || `request failed (${r.status})`);
        return;
      }
      toast.success(editingId ? "collection updated" : "collection created");
      resetEditor();
      await fetchList();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [title, note, items, editingId, fetchList, resetEditor]);

  const remove = useCallback(
    async (cid: string) => {
      if (!confirm("delete this collection? the public link will stop working.")) return;
      setBusyId(cid);
      try {
        const r = await fetch(`/api/collections/${encodeURIComponent(cid)}`, {
          method: "DELETE",
          headers: authHeaders(),
        });
        if (!r.ok) { toast.error(`delete failed (${r.status})`); return; }
        toast.success("collection deleted");
        if (editingId === cid) resetEditor();
        await fetchList();
      } finally { setBusyId(null); }
    },
    [fetchList, editingId, resetEditor],
  );

  const copyLink = useCallback(async (cid: string) => {
    const url = `${window.location.origin}/c/${cid}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedId(cid);
      setTimeout(() => setCopiedId((v) => (v === cid ? null : v)), 1500);
      toast.success("link copied");
    } catch { toast.error("could not copy"); }
  }, []);

  const isEdit = editingId !== null;
  const usedHistoryIds = useMemo(() => {
    const s = new Set<string>();
    items.forEach((it) => { if (it.query_id) s.add(it.query_id); });
    return s;
  }, [items]);

  return (
    <div className="px-4 py-6 max-w-6xl mx-auto space-y-5">
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
            <Stack size={12} weight="duotone" />
            <span>collections</span>
          </div>
          <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-text)]">
            grouped result sets
          </h1>
          <p className="font-mono text-[11px] text-[var(--color-muted)] mt-1">
            bundle saved matches into a public, shareable URL at /c/&lt;id&gt;.
          </p>
        </div>
        <button
          type="button"
          onClick={fetchList}
          className="px-2 py-1 border border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)]"
        >
          <ArrowsClockwise size={11} weight="duotone" className="inline mr-1" />
          refresh
        </button>
      </header>

      {!apiKey ? (
        <div className="panel rounded-[2px] px-4 py-6">
          <div className="flex items-center gap-2 font-mono text-[11px] text-[var(--color-muted)]">
            <Key size={14} weight="duotone" />
            <span>
              add your api key on the{" "}
              <Link href="/settings" className="text-[var(--color-phosphor)] hover:underline">
                settings page
              </Link>{" "}
              to start curating collections.
            </span>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="panel rounded-[2px]">
          <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between">
            <span className="label-xs">your collections</span>
            <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
              {list ? `${list.length}` : "-"}
            </span>
          </div>
          {listLoading && !list ? (
            <div className="px-4 py-6 space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-8 bg-[var(--color-panel)] animate-pulse rounded-[1px]" />
              ))}
            </div>
          ) : listErr ? (
            <div className="px-4 py-6 font-mono text-[11px] text-[var(--color-magenta)]">{listErr}</div>
          ) : !list || list.length === 0 ? (
            <div className="px-4 py-8 font-mono text-[11px] text-[var(--color-muted)] text-center">
              no collections yet. build one on the right.
            </div>
          ) : (
            <ul className="divide-y divide-[var(--color-line)]">
              {list.map((c) => (
                <li
                  key={c.id}
                  className={`px-3 py-2 flex items-center gap-2 ${editingId === c.id ? "bg-[var(--color-panel)]" : ""}`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-[12px] text-[var(--color-text)] truncate">{c.title}</div>
                    <div className="font-mono text-[10px] text-[var(--color-muted)] uppercase tracking-widest truncate">
                      {c.item_count} items / {fmtRel(c.updated_at)} / /c/{c.id}
                    </div>
                  </div>
                  <button type="button" onClick={() => copyLink(c.id)} title="copy public link" aria-label="copy public link" className="p-1.5 text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
                    {copiedId === c.id ? <Check size={14} weight="duotone" /> : <Copy size={14} weight="duotone" />}
                  </button>
                  <Link href={`/c/${c.id}`} target="_blank" title="open public view" aria-label="open public view" className="p-1.5 text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
                    <ArrowSquareOut size={14} weight="duotone" />
                  </Link>
                  <button type="button" onClick={() => loadForEdit(c.id)} title="edit" aria-label="edit" className="p-1.5 text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
                    <PencilSimple size={14} weight="duotone" />
                  </button>
                  <button type="button" onClick={() => remove(c.id)} disabled={busyId === c.id} title="delete" aria-label="delete" className="p-1.5 text-[var(--color-muted)] hover:text-[var(--color-magenta)] disabled:opacity-40">
                    <Trash size={14} weight="duotone" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel rounded-[2px]">
          <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between">
            <span className="label-xs">{isEdit ? `editing / ${editingId}` : "new collection"}</span>
            {isEdit ? (
              <button type="button" onClick={resetEditor} className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
                <X size={11} weight="duotone" className="inline mr-1" />cancel
              </button>
            ) : null}
          </div>
          <div className="p-3 space-y-3">
            <div>
              <label className="label-xs block mb-1" htmlFor="c-title">title</label>
              <input id="c-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="my top humming guesses" maxLength={MAX_TITLE}
                className="w-full bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]" />
              <div className="text-right font-mono text-[10px] text-[var(--color-dim)] mt-0.5">{title.length}/{MAX_TITLE}</div>
            </div>
            <div>
              <label className="label-xs block mb-1" htmlFor="c-note">note (optional)</label>
              <textarea id="c-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="a sentence about what this collection demonstrates" maxLength={MAX_NOTE} rows={2}
                className="w-full bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[11px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)] resize-none" />
              <div className="text-right font-mono text-[10px] text-[var(--color-dim)] mt-0.5">{note.length}/{MAX_NOTE}</div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="label-xs">items / {items.length}</span>
                {items.length >= MAX_ITEMS ? (
                  <span className="font-mono text-[10px] text-[var(--color-magenta)] uppercase tracking-widest">max reached</span>
                ) : null}
              </div>
              {items.length === 0 ? (
                <div className="border border-dashed border-[var(--color-line)] px-3 py-6 text-center font-mono text-[11px] text-[var(--color-muted)]">
                  pick rows from your history below.
                </div>
              ) : (
                <ol className="space-y-1.5">
                  {items.map((it, idx) => (
                    <li key={`${it.query_id || "item"}-${idx}`} className="border border-[var(--color-line)] px-2 py-1.5 flex items-center gap-2">
                      <span className="font-mono text-[10px] text-[var(--color-dim)] tabular-nums w-5">{String(idx + 1).padStart(2, "0")}</span>
                      <input type="text" value={it.label} onChange={(e) => renameItem(idx, e.target.value)} maxLength={MAX_TITLE} aria-label={`label for item ${idx + 1}`}
                        className="flex-1 bg-transparent font-mono text-[11px] text-[var(--color-text)] focus:outline-none truncate" />
                      <span className="font-mono text-[10px] text-[var(--color-dim)] hidden sm:inline">{it.results.length} cand</span>
                      <button type="button" onClick={() => moveItem(idx, -1)} disabled={idx === 0} title="move up" aria-label="move up" className="p-1 text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-30">
                        <CaretUp size={12} weight="duotone" />
                      </button>
                      <button type="button" onClick={() => moveItem(idx, 1)} disabled={idx === items.length - 1} title="move down" aria-label="move down" className="p-1 text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-30">
                        <CaretDown size={12} weight="duotone" />
                      </button>
                      <button type="button" onClick={() => removeItem(idx)} title="remove" aria-label="remove" className="p-1 text-[var(--color-muted)] hover:text-[var(--color-magenta)]">
                        <X size={12} weight="duotone" />
                      </button>
                    </li>
                  ))}
                </ol>
              )}
            </div>

            {saveErr ? <div className="font-mono text-[11px] text-[var(--color-magenta)]">{saveErr}</div> : null}

            <div className="flex justify-end gap-2 pt-1">
              {isEdit ? (
                <button type="button" onClick={resetEditor} className="px-3 py-1.5 border border-[var(--color-line)] font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]">reset</button>
              ) : null}
              <button type="button" onClick={save} disabled={saving} className="px-3 py-1.5 border border-[var(--color-phosphor)] font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-phosphor)] hover:text-[var(--color-bg)] disabled:opacity-40">
                <FloppyDisk size={12} weight="duotone" className="inline mr-1 -mt-0.5" />
                {saving ? "saving..." : isEdit ? "save changes" : "create collection"}
              </button>
            </div>
          </div>
        </section>
      </div>

      <section className="panel rounded-[2px]">
        <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between">
          <span className="label-xs">pick from your recent history</span>
          <button type="button" onClick={fetchHistory} className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
            <ArrowsClockwise size={11} weight="duotone" className="inline mr-1" />reload
          </button>
        </div>
        {historyErr ? (
          <div className="px-4 py-6 font-mono text-[11px] text-[var(--color-magenta)]">{historyErr}</div>
        ) : history === null ? (
          <div className="px-4 py-6 space-y-2">
            {[0, 1, 2, 3].map((i) => <div key={i} className="h-7 bg-[var(--color-panel)] animate-pulse rounded-[1px]" />)}
          </div>
        ) : history.length === 0 ? (
          <div className="px-4 py-8 font-mono text-[11px] text-[var(--color-muted)] text-center">
            no cloud history yet. record a hum on the home page first.
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-line)] max-h-[420px] overflow-y-auto">
            {history.map((h) => {
              const top = h.results[0];
              const already = usedHistoryIds.has(h.query_id);
              return (
                <li key={h.id} className="px-3 py-2 flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-[12px] text-[var(--color-text)] truncate">
                      {h.name || top?.title || h.filename || "untitled"}
                      {top?.artist ? <span className="text-[var(--color-muted)]"> / {top.artist}</span> : null}
                    </div>
                    <div className="font-mono text-[10px] text-[var(--color-muted)] uppercase tracking-widest truncate">
                      {h.count} cand / {h.elapsed_ms} ms / {fmtRel(h.created_at)}{h.starred ? " / starred" : ""}
                    </div>
                  </div>
                  <button type="button" onClick={() => addFromHistory(h)} disabled={already || items.length >= MAX_ITEMS}
                    className="px-2 py-1 border border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)] disabled:opacity-40 disabled:cursor-not-allowed">
                    <Plus size={11} weight="duotone" className="inline mr-1" />{already ? "added" : "add"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
