"use client";

/**
 * SavedViews
 *
 * Renders the user's saved history-filter combinations as quick-apply
 * chips. The active filters can be saved as a new view, and any
 * existing view can be applied, renamed, or deleted. Server-backed
 * via /api/history/views; the API key is attached automatically by
 * lib/apiKey's fetch patch.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Bookmark,
  BookmarkSimple,
  FloppyDisk,
  Trash,
  Check,
  X,
} from "@phosphor-icons/react/dist/ssr";

export type SortKey = "recent" | "oldest" | "name" | "results" | "top_score";

export interface ViewFilters {
  q: string;
  tag: string;
  sort: SortKey;
  starred: boolean;
}

interface ViewItem {
  id: string;
  name: string;
  filters: ViewFilters;
  created_at: number;
  updated_at: number;
}

interface ListResponse {
  items: ViewItem[];
  total: number;
}

function filtersEqual(a: ViewFilters, b: ViewFilters): boolean {
  return (
    a.q.trim() === b.q.trim() &&
    a.tag.trim().toLowerCase() === b.tag.trim().toLowerCase() &&
    a.sort === b.sort &&
    a.starred === b.starred
  );
}

function filtersAreDefault(f: ViewFilters): boolean {
  return !f.q.trim() && !f.tag.trim() && f.sort === "recent" && !f.starred;
}

function summarize(f: ViewFilters): string {
  const bits: string[] = [];
  if (f.q.trim()) bits.push(`"${f.q.trim()}"`);
  if (f.tag.trim()) bits.push(`#${f.tag.trim()}`);
  if (f.starred) bits.push("starred");
  if (f.sort !== "recent") bits.push(f.sort.replace("_", " "));
  return bits.length ? bits.join(" / ") : "no filters";
}

interface Props {
  current: ViewFilters;
  onApply: (f: ViewFilters) => void;
  hasApiKey: boolean;
}

export default function SavedViews({ current, onApply, hasApiKey }: Props) {
  const [views, setViews] = useState<ViewItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [naming, setNaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const load = useCallback(async () => {
    if (!hasApiKey) {
      setViews([]);
      return;
    }
    try {
      const r = await fetch("/api/history/views", { cache: "no-store" });
      if (r.status === 401) {
        setViews([]);
        return;
      }
      if (!r.ok) throw new Error(`http ${r.status}`);
      const j = (await r.json()) as ListResponse;
      setViews(j.items || []);
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || "failed to load views");
      setViews([]);
    }
  }, [hasApiKey]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    const name = draft.trim();
    if (!name) return;
    setBusy(true);
    try {
      const r = await fetch("/api/history/views", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          filters: {
            q: current.q.trim(),
            tag: current.tag.trim().toLowerCase(),
            sort: current.sort,
            starred: current.starred,
          },
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j?.detail || `save failed (${r.status})`);
      }
      setNaming(false);
      setDraft("");
      await load();
    } catch (e: any) {
      setErr(e?.message || "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function rename(id: string) {
    const name = renameDraft.trim();
    if (!name) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/history/views/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j?.detail || `rename failed (${r.status})`);
      }
      setRenamingId(null);
      setRenameDraft("");
      await load();
    } catch (e: any) {
      setErr(e?.message || "rename failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this saved view?")) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/history/views/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`delete failed (${r.status})`);
      await load();
    } catch (e: any) {
      setErr(e?.message || "delete failed");
    } finally {
      setBusy(false);
    }
  }

  // Identify a view that matches the active filters so we can show
  // the user which one is in use.
  const activeView = (views || []).find((v) => filtersEqual(v.filters, current));
  const canSave = hasApiKey && !filtersAreDefault(current) && !activeView;

  if (!hasApiKey) {
    return null;
  }

  return (
    <div className="panel rounded-[2px] p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
          <Bookmark size={12} weight="duotone" /> saved views
        </div>
        <div className="flex items-center gap-2">
          {!naming ? (
            <button
              type="button"
              onClick={() => {
                setNaming(true);
                setDraft("");
              }}
              disabled={!canSave || busy}
              title={
                activeView
                  ? `current filters match "${activeView.name}"`
                  : filtersAreDefault(current)
                    ? "apply some filters first"
                    : "save current filters"
              }
              className="font-mono text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] disabled:opacity-40 flex items-center gap-1"
            >
              <FloppyDisk size={12} weight="duotone" /> save current
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="view name"
                maxLength={80}
                className="bg-[var(--color-bg)] border border-[var(--color-line)] font-mono text-[11px] px-2 py-1 w-44"
                onKeyDown={(e) => {
                  if (e.key === "Enter") save();
                  if (e.key === "Escape") {
                    setNaming(false);
                    setDraft("");
                  }
                }}
              />
              <button
                type="button"
                onClick={save}
                disabled={!draft.trim() || busy}
                aria-label="confirm save"
                className="p-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] disabled:opacity-40"
              >
                <Check size={12} weight="duotone" />
              </button>
              <button
                type="button"
                onClick={() => {
                  setNaming(false);
                  setDraft("");
                }}
                aria-label="cancel save"
                className="p-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)]"
              >
                <X size={12} weight="duotone" />
              </button>
            </div>
          )}
        </div>
      </div>

      {err && (
        <div className="font-mono text-[10px] text-[var(--color-amber)] mb-2">
          {err}
        </div>
      )}

      {views === null ? (
        <div className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
          loading...
        </div>
      ) : views.length === 0 ? (
        <div className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
          no saved views. set some filters, then save current to pin them here.
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {views.map((v) => {
            const isActive = activeView?.id === v.id;
            const isRenaming = renamingId === v.id;
            if (isRenaming) {
              return (
                <div key={v.id} className="flex items-center gap-1 border border-[var(--color-line)] px-1.5 py-0.5">
                  <input
                    autoFocus
                    value={renameDraft}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    maxLength={80}
                    className="bg-[var(--color-bg)] font-mono text-[11px] outline-none w-36"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") rename(v.id);
                      if (e.key === "Escape") {
                        setRenamingId(null);
                        setRenameDraft("");
                      }
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => rename(v.id)}
                    disabled={!renameDraft.trim() || busy}
                    aria-label="confirm rename"
                    className="p-0.5 hover:text-[var(--color-phosphor)] disabled:opacity-40"
                  >
                    <Check size={11} weight="duotone" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setRenamingId(null);
                      setRenameDraft("");
                    }}
                    aria-label="cancel rename"
                    className="p-0.5 hover:text-[var(--color-amber)]"
                  >
                    <X size={11} weight="duotone" />
                  </button>
                </div>
              );
            }
            return (
              <div
                key={v.id}
                className={`group flex items-center gap-1 border px-2 py-0.5 font-mono text-[11px] ${
                  isActive
                    ? "border-[var(--color-phosphor)] bg-[rgba(29,185,84,0.06)] text-[var(--color-phosphor)]"
                    : "border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-line-2)]"
                }`}
                title={summarize(v.filters)}
              >
                <button
                  type="button"
                  onClick={() => onApply(v.filters)}
                  className="flex items-center gap-1"
                >
                  <BookmarkSimple size={11} weight={isActive ? "fill" : "duotone"} />
                  <span>{v.name}</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setRenamingId(v.id);
                    setRenameDraft(v.name);
                  }}
                  aria-label={`rename ${v.name}`}
                  className="opacity-0 group-hover:opacity-100 hover:text-[var(--color-text)] px-0.5"
                  title="rename"
                >
                  <span aria-hidden className="text-[9px]">ren</span>
                </button>
                <button
                  type="button"
                  onClick={() => remove(v.id)}
                  aria-label={`delete ${v.name}`}
                  className="opacity-0 group-hover:opacity-100 hover:text-[var(--color-amber)] px-0.5"
                  title="delete"
                >
                  <Trash size={11} weight="duotone" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
