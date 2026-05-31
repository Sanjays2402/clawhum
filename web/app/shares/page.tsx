"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ShareNetwork,
  Trash,
  Copy,
  ArrowsClockwise,
  Clock,
  MusicNote,
  Key,
  ArrowSquareOut,
  MagnifyingGlass,
  Check,
  PencilSimple,
  X,
} from "@phosphor-icons/react/dist/ssr";
import { useApiKey, getApiKey } from "@/lib/apiKey";
import { toast } from "@/lib/toast";

interface ShareItem {
  id: string;
  created_at: number;
  query_id: string;
  elapsed_ms: number;
  count: number;
  filename: string | null;
  duration_sec: number | null;
  note: string | null;
  top_title: string | null;
  top_artist: string | null;
  top_score: number | null;
  url_path: string;
}

interface ShareList {
  shares: ShareItem[];
  total: number;
}

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

export default function SharesPage() {
  const [apiKey] = useApiKey();
  const [data, setData] = useState<ShareList | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);

  const fetchShares = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const headers: Record<string, string> = {};
      const k = getApiKey();
      if (k) headers["X-API-Key"] = k;
      const r = await fetch("/api/share", { headers, cache: "no-store" });
      if (r.status === 401) {
        setErr("api key required. set one on the settings page.");
        setData(null);
        return;
      }
      if (!r.ok) {
        const body = await r.text();
        setErr(body.slice(0, 240) || `request failed (${r.status})`);
        setData(null);
        return;
      }
      const body = (await r.json()) as ShareList;
      setData(body);
    } catch (e) {
      setErr((e as Error).message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchShares();
  }, [fetchShares, apiKey]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return data.shares;
    return data.shares.filter((s) => {
      const hay = [
        s.id,
        s.top_title,
        s.top_artist,
        s.filename,
        s.note,
        s.query_id,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [data, q]);

  const copyLink = useCallback(async (id: string) => {
    const url =
      typeof window !== "undefined"
        ? `${window.location.origin}/r/${id}`
        : `/r/${id}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedId(id);
      setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 1400);
      toast.success("link copied", { description: url });
    } catch {
      toast.error("clipboard blocked");
    }
  }, []);

  const startEdit = useCallback((s: ShareItem) => {
    setEditingId(s.id);
    setNoteDraft(s.note ?? "");
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setNoteDraft("");
  }, []);

  const saveNote = useCallback(
    async (id: string) => {
      const next = noteDraft.slice(0, 280);
      setSavingId(id);
      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        const k = getApiKey();
        if (k) headers["X-API-Key"] = k;
        const r = await fetch(`/api/share/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers,
          body: JSON.stringify({ note: next }),
        });
        if (!r.ok) {
          const body = await r.text();
          toast.error("rename failed", { description: body.slice(0, 200) });
          return;
        }
        const updated = (await r.json()) as ShareItem;
        setData((cur) =>
          cur
            ? {
                ...cur,
                shares: cur.shares.map((s) =>
                  s.id === id ? { ...s, note: updated.note } : s,
                ),
              }
            : cur,
        );
        toast.success(updated.note ? "note updated" : "note cleared");
        setEditingId(null);
        setNoteDraft("");
      } catch (e) {
        toast.error("rename failed", { description: (e as Error).message });
      } finally {
        setSavingId(null);
      }
    },
    [noteDraft],
  );

  const revoke = useCallback(
    async (id: string) => {
      if (!confirm(`revoke share ${id}? the public link will stop working.`)) return;
      setBusyId(id);
      try {
        const headers: Record<string, string> = {};
        const k = getApiKey();
        if (k) headers["X-API-Key"] = k;
        const r = await fetch(`/api/share/${encodeURIComponent(id)}`, {
          method: "DELETE",
          headers,
        });
        if (!r.ok) {
          const body = await r.text();
          toast.error("revoke failed", { description: body.slice(0, 200) });
          return;
        }
        toast.success("share revoked");
        // Optimistic remove, then resync.
        setData((cur) =>
          cur
            ? {
                shares: cur.shares.filter((s) => s.id !== id),
                total: Math.max(0, cur.total - 1),
              }
            : cur,
        );
        fetchShares();
      } catch (e) {
        toast.error("revoke failed", { description: (e as Error).message });
      } finally {
        setBusyId(null);
      }
    },
    [fetchShares],
  );

  const hasKey = !!apiKey;

  return (
    <div className="px-4 py-6 max-w-6xl mx-auto space-y-5">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <ShareNetwork size={12} weight="duotone" />
          <span>public share links</span>
        </div>
        <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-text)]">
          your shared matches
        </h1>
        <p className="font-mono text-[10px] text-[var(--color-muted)] uppercase tracking-widest">
          read-only links you have created. revoke any to break the public url.
        </p>
      </header>

      <section className="panel rounded-[2px]">
        <div className="px-3 py-2 border-b border-[var(--color-line)] flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 flex-1 min-w-[200px]">
            <MagnifyingGlass size={12} weight="duotone" className="text-[var(--color-dim)]" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="filter by id, title, artist, note"
              aria-label="filter shares"
              className="bg-transparent border-0 outline-none font-mono text-[11px] text-[var(--color-text)] placeholder:text-[var(--color-dim)] flex-1"
            />
          </div>
          <button
            type="button"
            onClick={fetchShares}
            disabled={loading}
            className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-50"
          >
            <ArrowsClockwise size={11} weight="duotone" className={loading ? "animate-spin" : ""} />
            <span>refresh</span>
          </button>
          <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
            {data ? `${filtered.length} / ${data.total}` : "/"}
          </span>
        </div>

        {!hasKey && (
          <div className="px-4 py-6 flex items-start gap-3 border-b border-[var(--color-line)]">
            <Key size={14} weight="duotone" className="text-[var(--color-magenta)] mt-0.5" />
            <div className="font-mono text-[11px] text-[var(--color-muted)] space-y-1">
              <div className="text-[var(--color-text)] uppercase tracking-widest text-[10px]">
                no api key set
              </div>
              <div>
                shares are scoped to your tenant. set a key on{" "}
                <Link href="/settings" className="text-[var(--color-phosphor)] underline">
                  settings
                </Link>{" "}
                to see your list.
              </div>
            </div>
          </div>
        )}

        {err && (
          <div className="px-4 py-3 font-mono text-[11px] text-[var(--color-magenta)] border-b border-[var(--color-line)]">
            error: {err}
          </div>
        )}

        {loading && !data && (
          <ul className="divide-y divide-[var(--color-line)]">
            {Array.from({ length: 4 }).map((_, i) => (
              <li key={i} className="px-3 py-3 animate-pulse">
                <div className="h-3 w-1/3 bg-[var(--color-line)] rounded-[1px] mb-2" />
                <div className="h-2 w-1/2 bg-[var(--color-line)] rounded-[1px]" />
              </li>
            ))}
          </ul>
        )}

        {!loading && data && filtered.length === 0 && (
          <div className="px-4 py-10 text-center space-y-2">
            <ShareNetwork
              size={22}
              weight="duotone"
              className="mx-auto text-[var(--color-dim)]"
            />
            <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
              {data.total === 0 ? "no shares yet" : "nothing matches that filter"}
            </div>
            {data.total === 0 && (
              <div className="font-mono text-[10px] text-[var(--color-dim)]">
                run a match on{" "}
                <Link href="/" className="text-[var(--color-phosphor)] underline">
                  capture
                </Link>{" "}
                then click share.
              </div>
            )}
          </div>
        )}

        {data && filtered.length > 0 && (
          <ul className="divide-y divide-[var(--color-line)]">
            {filtered.map((s) => (
              <li
                key={s.id}
                className="px-3 py-3 flex flex-col sm:flex-row sm:items-center gap-3"
              >
                <div className="flex items-start gap-3 min-w-0 flex-1">
                  <MusicNote
                    size={14}
                    weight="duotone"
                    className="text-[var(--color-phosphor)] mt-0.5 shrink-0"
                  />
                  <div className="min-w-0 flex-1">
                    <Link
                      href={s.url_path}
                      className="font-mono text-[12px] text-[var(--color-text)] hover:text-[var(--color-phosphor)] truncate block"
                    >
                      {s.top_title || "(no candidates)"}
                      {s.top_artist ? (
                        <span className="text-[var(--color-muted)]"> by {s.top_artist}</span>
                      ) : null}
                    </Link>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[9px] text-[var(--color-dim)] uppercase tracking-widest mt-0.5">
                      <span className="text-[var(--color-phosphor)]">{s.id}</span>
                      <span title={fmtTs(s.created_at)}>{fmtRel(s.created_at)}</span>
                      <span>
                        <Clock size={9} weight="duotone" className="inline mr-0.5 -mt-px" />
                        {s.elapsed_ms}ms
                      </span>
                      <span>n={s.count}</span>
                      {typeof s.top_score === "number" && (
                        <span>score {s.top_score.toFixed(3)}</span>
                      )}
                      {s.filename && <span className="truncate max-w-[140px]">{s.filename}</span>}
                    </div>
                    {editingId === s.id ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          saveNote(s.id);
                        }}
                        className="mt-1 flex items-center gap-1.5"
                      >
                        <input
                          autoFocus
                          value={noteDraft}
                          maxLength={280}
                          onChange={(e) => setNoteDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.preventDefault();
                              cancelEdit();
                            }
                          }}
                          placeholder="add a short note, 280 chars max"
                          aria-label={`edit note for share ${s.id}`}
                          className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] focus:border-[var(--color-phosphor)] outline-none px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text)] placeholder:text-[var(--color-dim)] rounded-[1px]"
                        />
                        <span className="font-mono text-[9px] text-[var(--color-dim)] tabular-nums">
                          {noteDraft.length}/280
                        </span>
                        <button
                          type="submit"
                          disabled={savingId === s.id}
                          aria-label="save note"
                          className="flex items-center gap-1 px-1.5 py-0.5 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[9px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] rounded-[1px] disabled:opacity-50"
                        >
                          <Check size={10} weight="duotone" />
                          <span>{savingId === s.id ? "..." : "save"}</span>
                        </button>
                        <button
                          type="button"
                          onClick={cancelEdit}
                          aria-label="cancel edit"
                          className="flex items-center gap-1 px-1.5 py-0.5 border border-[var(--color-line)] hover:border-[var(--color-magenta)] font-mono text-[9px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-magenta)] rounded-[1px]"
                        >
                          <X size={10} weight="duotone" />
                          <span>cancel</span>
                        </button>
                      </form>
                    ) : (
                      <button
                        type="button"
                        onClick={() => startEdit(s)}
                        aria-label={s.note ? `edit note for share ${s.id}` : `add note to share ${s.id}`}
                        className="group mt-1 flex items-center gap-1.5 text-left max-w-full"
                      >
                        {s.note ? (
                          <span className="font-mono text-[10px] text-[var(--color-muted)] italic truncate group-hover:text-[var(--color-text)]">
                            {s.note}
                          </span>
                        ) : (
                          <span className="font-mono text-[10px] text-[var(--color-dim)] italic group-hover:text-[var(--color-muted)]">
                            add a note
                          </span>
                        )}
                        <PencilSimple
                          size={10}
                          weight="duotone"
                          className="text-[var(--color-dim)] opacity-0 group-hover:opacity-100 shrink-0"
                        />
                      </button>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 sm:shrink-0">
                  <Link
                    href={s.url_path}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`open share ${s.id}`}
                    className="flex items-center gap-1 px-2 py-1 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] rounded-[1px]"
                  >
                    <ArrowSquareOut size={11} weight="duotone" />
                    <span>open</span>
                  </Link>
                  <button
                    type="button"
                    onClick={() => copyLink(s.id)}
                    aria-label={`copy link for ${s.id}`}
                    className="flex items-center gap-1 px-2 py-1 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] rounded-[1px]"
                  >
                    {copiedId === s.id ? (
                      <>
                        <Check size={11} weight="duotone" />
                        <span>copied</span>
                      </>
                    ) : (
                      <>
                        <Copy size={11} weight="duotone" />
                        <span>copy</span>
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => revoke(s.id)}
                    disabled={busyId === s.id}
                    aria-label={`revoke share ${s.id}`}
                    className="flex items-center gap-1 px-2 py-1 border border-[var(--color-line)] hover:border-[var(--color-magenta)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-magenta)] rounded-[1px] disabled:opacity-50"
                  >
                    <Trash size={11} weight="duotone" />
                    <span>{busyId === s.id ? "..." : "revoke"}</span>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <footer className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
        revoked links return 404 on /r/&lt;id&gt; and disappear from search engines on next crawl.
      </footer>
    </div>
  );
}
