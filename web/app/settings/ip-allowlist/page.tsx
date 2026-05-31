"use client";

/**
 * Workspace IP allowlist administration.
 *
 * Admins add CIDR ranges that the API enforces on every authenticated
 * request from this workspace. An empty list means "no restriction" so
 * the page also makes the opt-in nature explicit and shows the
 * caller's detected client IP for quick self-debug.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  Plus,
  Trash,
  Warning,
  ArrowLeft,
  Globe,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface Rule {
  id: string;
  cidr: string;
  label: string;
  created_at: number;
}

interface ListResp {
  enforcing: boolean;
  rules: Rule[];
  your_ip: string;
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
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export default function IpAllowlistPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [cidr, setCidr] = useState("");
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/ip-allowlist", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as ListResp;
      setState({ kind: "ready", data });
    } catch (err) {
      setState({ kind: "error", status: 0, message: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!cidr.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/ip-allowlist", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ cidr: cidr.trim(), label: label.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setCreateError(body.detail || `Request failed (${r.status})`);
        return;
      }
      setCidr("");
      setLabel("");
      await refresh();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function onDelete(id: string) {
    setDeleting(id);
    try {
      const r = await fetch(`/api/ip-allowlist/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!r.ok && r.status !== 204) {
        const body = await r.text();
        setCreateError(body || `Delete failed (${r.status})`);
        return;
      }
      setConfirmId(null);
      await refresh();
    } finally {
      setDeleting(null);
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:py-14">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        <ArrowLeft size={14} weight="duotone" />
        Settings
      </Link>

      <header className="mt-4 flex items-start gap-3">
        <ShieldCheck size={28} weight="duotone" className="mt-1 text-emerald-500" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">IP allowlist</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Restrict workspace API access to a list of trusted CIDR ranges.
            Leave the list empty for no restriction.
          </p>
        </div>
      </header>

      {state.kind === "ready" ? (
        <div className="mt-6 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/50 dark:text-zinc-400">
          <Globe size={14} weight="duotone" />
          <span>
            Your current IP looks like{" "}
            <code className="rounded bg-white px-1 py-0.5 font-mono text-[11px] text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
              {state.data.your_ip}
            </code>
          </span>
          <span className="ml-auto inline-flex items-center gap-1">
            <LockKey size={14} weight="duotone" />
            {state.data.enforcing ? "Enforcing" : "Not enforcing"}
          </span>
        </div>
      ) : null}

      <section className="mt-6 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
        <h2 className="text-sm font-medium">Add a rule</h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Accepts IPv4 or IPv6 CIDR notation. A single address is fine
          (use /32 for IPv4, /128 for IPv6).
        </p>
        <form onSubmit={onCreate} className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            value={cidr}
            onChange={(e) => setCidr(e.target.value)}
            placeholder="10.0.0.0/24"
            aria-label="CIDR range"
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-zinc-700 dark:bg-zinc-900"
            required
          />
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="office vpn (optional)"
            aria-label="Label"
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-zinc-700 dark:bg-zinc-900"
            maxLength={120}
          />
          <button
            type="submit"
            disabled={creating || !cidr.trim()}
            className="inline-flex items-center justify-center gap-1.5 rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
          >
            <Plus size={14} weight="duotone" />
            {creating ? "Adding" : "Add rule"}
          </button>
        </form>
        {createError ? (
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-rose-50 px-2 py-1 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
            <Warning size={12} weight="duotone" />
            {createError}
          </div>
        ) : null}
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-medium">Current rules</h2>

        {state.kind === "loading" ? (
          <ul className="mt-3 space-y-2">
            {[0, 1].map((i) => (
              <li
                key={i}
                className="h-14 animate-pulse rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50"
              />
            ))}
          </ul>
        ) : null}

        {state.kind === "error" ? (
          <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
            <div className="flex items-center gap-2 font-medium">
              <Warning size={14} weight="duotone" />
              {state.status === 403
                ? "Admin role required to manage the allowlist."
                : state.status === 401
                ? "Sign in with an admin API key to manage the allowlist."
                : `Could not load rules (${state.status || "network"}).`}
            </div>
            <div className="mt-1 break-words text-xs">{state.message}</div>
          </div>
        ) : null}

        {state.kind === "ready" && state.data.rules.length === 0 ? (
          <div className="mt-3 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/40">
            No rules configured. Every authenticated request is allowed.
          </div>
        ) : null}

        {state.kind === "ready" && state.data.rules.length > 0 ? (
          <ul className="mt-3 divide-y divide-zinc-200 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950">
            {state.data.rules.map((r) => (
              <li key={r.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center">
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm">{r.cidr}</div>
                  <div className="mt-0.5 truncate text-xs text-zinc-500 dark:text-zinc-400">
                    {r.label || "no label"}
                    {r.created_at ? ` • added ${formatTs(r.created_at)}` : ""}
                  </div>
                </div>
                {confirmId === r.id ? (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-600 dark:text-zinc-400">Remove?</span>
                    <button
                      type="button"
                      onClick={() => onDelete(r.id)}
                      disabled={deleting === r.id}
                      className="rounded-md bg-rose-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-rose-500 disabled:opacity-50"
                    >
                      {deleting === r.id ? "Removing" : "Confirm"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmId(null)}
                      className="rounded-md border border-zinc-300 px-2.5 py-1.5 text-xs hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmId(r.id)}
                    className="inline-flex items-center gap-1 self-start rounded-md border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-700 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700 dark:border-zinc-800 dark:text-zinc-300 dark:hover:border-rose-900 dark:hover:bg-rose-950/40 dark:hover:text-rose-300 sm:self-auto"
                  >
                    <Trash size={12} weight="duotone" />
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <p className="mt-8 text-xs text-zinc-500 dark:text-zinc-400">
        Enforcement looks at the first hop in <code className="font-mono">X-Forwarded-For</code> when
        present, otherwise the socket peer. Make sure your ingress strips
        untrusted client headers before this reaches the API.
      </p>
    </main>
  );
}
