"use client";

/**
 * Workspace embed origin allowlist administration.
 *
 * Admins register the sites that are allowed to frame this
 * workspace's public share embeds. An empty list means no
 * restriction so the feature is strictly opt-in and existing
 * embeds keep working unchanged.
 *
 * Enforcement points wired into the same allowlist:
 *  - GET /share/{id} rejects browser reads from non-allowed origins
 *  - GET /api/oembed rejects oEmbed calls from non-allowed origins
 *  - /r/{id}/embed responds with a frame-ancestors CSP narrowed to
 *    the allowed origins, so a hostile site cannot iframe the embed.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Browsers,
  Plus,
  Trash,
  Warning,
  ArrowLeft,
  ShieldCheck,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface OriginRow {
  id: string;
  origin: string;
  label: string;
  created_at: number;
}

interface ListResp {
  enforcing: boolean;
  origins: OriginRow[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ListResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

export default function EmbedOriginsPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [origin, setOrigin] = useState("");
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/embed-origins", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as ListResp;
      setState({ kind: "ready", data });
    } catch (err) {
      setState({
        kind: "error",
        status: 0,
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!origin.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/embed-origins", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ origin: origin.trim(), label: label.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setCreateError(body.detail || `Request failed (${r.status})`);
        return;
      }
      setOrigin("");
      setLabel("");
      await refresh();
    } finally {
      setCreating(false);
    }
  }

  async function onDelete(id: string) {
    setDeleting(id);
    try {
      const r = await fetch(`/api/embed-origins/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!r.ok && r.status !== 204) return;
      setConfirmId(null);
      await refresh();
    } finally {
      setDeleting(null);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 text-zinc-100">
      <div className="mb-6 flex items-center gap-3 text-sm text-zinc-400">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 hover:text-zinc-200"
        >
          <ArrowLeft size={16} weight="duotone" /> settings
        </Link>
        <span>/</span>
        <span className="text-zinc-200">embed origins</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Browsers size={26} weight="duotone" className="text-indigo-300" />
          Embed origin allowlist
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Lock down which sites may frame your workspace's public share
          embeds and call the oEmbed endpoint. Empty list means no
          restriction so existing embeds keep working.
        </p>
      </header>

      <section
        aria-labelledby="add-origin"
        className="mb-8 rounded-xl border border-zinc-800 bg-zinc-950/60 p-5"
      >
        <h2 id="add-origin" className="mb-4 flex items-center gap-2 text-sm font-medium text-zinc-200">
          <Plus size={16} weight="duotone" /> Add an origin
        </h2>
        <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-[1fr,200px,auto]">
          <input
            type="text"
            inputMode="url"
            required
            placeholder="https://docs.acme.com"
            value={origin}
            onChange={(e) => setOrigin(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-indigo-500 focus:outline-none"
            aria-label="origin URL"
          />
          <input
            type="text"
            placeholder="label (optional)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={120}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-indigo-500 focus:outline-none"
            aria-label="label"
          />
          <button
            type="submit"
            disabled={creating || !origin.trim()}
            className="inline-flex items-center justify-center gap-1 rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <LockKey size={16} weight="duotone" />
            {creating ? "adding" : "add"}
          </button>
        </form>
        {createError && (
          <p className="mt-3 flex items-center gap-1 text-xs text-rose-400">
            <Warning size={14} weight="duotone" /> {createError}
          </p>
        )}
        <p className="mt-3 text-xs text-zinc-500">
          Scheme plus host, no path. The port is optional. Example:
          <code className="ml-1 rounded bg-zinc-900 px-1.5 py-0.5 text-[11px] text-zinc-300">
            https://docs.acme.com
          </code>
        </p>
      </section>

      <section aria-labelledby="rules" className="rounded-xl border border-zinc-800 bg-zinc-950/60">
        <header className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <h2 id="rules" className="flex items-center gap-2 text-sm font-medium text-zinc-200">
            <ShieldCheck size={16} weight="duotone" /> Allowed origins
          </h2>
          {state.kind === "ready" && (
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                state.data.enforcing
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {state.data.enforcing ? "enforcing" : "no restriction"}
            </span>
          )}
        </header>

        {state.kind === "loading" && (
          <ul className="divide-y divide-zinc-900">
            {[0, 1, 2].map((i) => (
              <li key={i} className="flex items-center justify-between px-5 py-4">
                <div className="h-4 w-2/3 animate-pulse rounded bg-zinc-800" />
                <div className="h-4 w-12 animate-pulse rounded bg-zinc-800" />
              </li>
            ))}
          </ul>
        )}

        {state.kind === "error" && (
          <div className="flex items-start gap-3 px-5 py-6 text-sm text-rose-300">
            <Warning size={18} weight="duotone" />
            <div>
              <div className="font-medium">Could not load embed origins</div>
              <div className="mt-1 text-xs text-rose-200/70">
                {state.status ? `HTTP ${state.status} ` : ""}
                {state.message}
              </div>
            </div>
          </div>
        )}

        {state.kind === "ready" && state.data.origins.length === 0 && (
          <div className="px-5 py-10 text-center text-sm text-zinc-400">
            <Browsers size={28} weight="duotone" className="mx-auto mb-2 text-zinc-600" />
            No origins yet. Any site can embed your shares.
          </div>
        )}

        {state.kind === "ready" && state.data.origins.length > 0 && (
          <ul className="divide-y divide-zinc-900">
            {state.data.origins.map((o) => (
              <li key={o.id} className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm">
                    <code className="truncate text-zinc-100">{o.origin}</code>
                    {o.label && (
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400">
                        {o.label}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-zinc-500">
                    added {formatTs(o.created_at)}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {confirmId === o.id ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setConfirmId(null)}
                        className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900"
                      >
                        cancel
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelete(o.id)}
                        disabled={deleting === o.id}
                        className="inline-flex items-center gap-1 rounded-md bg-rose-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-400 disabled:opacity-50"
                      >
                        <Trash size={14} weight="duotone" />
                        {deleting === o.id ? "removing" : "confirm remove"}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmId(o.id)}
                      className="inline-flex items-center gap-1 rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:border-rose-500/60 hover:text-rose-300"
                    >
                      <Trash size={14} weight="duotone" /> remove
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
