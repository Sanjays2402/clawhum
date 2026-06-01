"use client";

/**
 * Workspace trusted reverse proxy administration.
 *
 * The deployment global CLAWHUM_TRUSTED_PROXIES_GLOBAL env names the
 * CIDRs the API trusts to set X-Forwarded-For for every workspace.
 * Each tenant can layer additional proxies on top (a VPN gateway,
 * their own ingress) without giving them the ability to remove the
 * operator's entries. The page surfaces both, plus a "what the API
 * thinks your client looks like" panel so SecOps can confirm the
 * proxy is wired correctly without leaving settings.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowsLeftRight,
  CheckCircle,
  Globe,
  Network,
  Plus,
  ShieldCheck,
  Trash,
  Warning,
  XCircle,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface ProxyRule {
  id: string;
  cidr: string;
  label: string;
  created_at: number;
}

interface ListResp {
  workspace_rules: ProxyRule[];
  global_cidrs: string[];
  your_socket_peer: string;
  your_resolved_ip: string;
  peer_is_trusted: boolean;
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

export default function TrustedProxiesPage() {
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
      const r = await fetch("/api/trusted-proxies", { headers: authHeaders() });
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
      const r = await fetch("/api/trusted-proxies", {
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
      const r = await fetch(`/api/trusted-proxies/${encodeURIComponent(id)}`, {
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
          <h1 className="text-2xl font-semibold tracking-tight">Trusted proxies</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            CIDR ranges the API trusts to set <code className="font-mono">X-Forwarded-For</code>{" "}
            on requests for this workspace. Without an entry that covers
            your ingress, the API ignores the header and falls back to
            the socket peer, which protects the IP allowlist from spoofed
            headers but will reject office IPs that arrive via a proxy.
          </p>
        </div>
      </header>

      {state.kind === "ready" ? (
        <div className="mt-6 grid grid-cols-1 gap-3 rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-xs dark:border-zinc-800 dark:bg-zinc-900/50 sm:grid-cols-2">
          <div className="flex items-center gap-2 text-zinc-600 dark:text-zinc-400">
            <Network size={14} weight="duotone" />
            <span className="min-w-0">
              Socket peer{" "}
              <code className="rounded bg-white px-1 py-0.5 font-mono text-[11px] text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
                {state.data.your_socket_peer || "unknown"}
              </code>
            </span>
          </div>
          <div className="flex items-center gap-2 text-zinc-600 dark:text-zinc-400">
            <ArrowsLeftRight size={14} weight="duotone" />
            <span className="min-w-0">
              Resolved client{" "}
              <code className="rounded bg-white px-1 py-0.5 font-mono text-[11px] text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
                {state.data.your_resolved_ip || "unknown"}
              </code>
            </span>
          </div>
          <div className="flex items-center gap-2 sm:col-span-2">
            {state.data.peer_is_trusted ? (
              <CheckCircle size={14} weight="duotone" className="text-emerald-500" />
            ) : (
              <XCircle size={14} weight="duotone" className="text-amber-500" />
            )}
            <span className="text-zinc-600 dark:text-zinc-400">
              {state.data.peer_is_trusted
                ? "Your socket peer is in the trusted set. X-Forwarded-For is being honoured."
                : "Your socket peer is NOT trusted. Any X-Forwarded-For header you send is ignored."}
            </span>
          </div>
        </div>
      ) : null}

      <section className="mt-6 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
        <h2 className="text-sm font-medium">Deployment global proxies</h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Set by the operator via <code className="font-mono">CLAWHUM_TRUSTED_PROXIES_GLOBAL</code>.
          Read only here so a workspace admin cannot widen or remove the
          baseline trust set.
        </p>
        {state.kind === "ready" ? (
          state.data.global_cidrs.length === 0 ? (
            <div className="mt-3 rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-3 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/40">
              None configured. The API is exposed directly and every
              X-Forwarded-For header is ignored.
            </div>
          ) : (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {state.data.global_cidrs.map((c) => (
                <li
                  key={c}
                  className="inline-flex items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 px-2 py-1 font-mono text-[11px] text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/50 dark:text-zinc-300"
                >
                  <Globe size={11} weight="duotone" />
                  {c}
                </li>
              ))}
            </ul>
          )
        ) : null}
      </section>

      <section className="mt-6 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
        <h2 className="text-sm font-medium">Add a workspace proxy</h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          IPv4 or IPv6 CIDR notation. A single address is fine; bare
          addresses become /32 or /128.
        </p>
        <form onSubmit={onCreate} className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            value={cidr}
            onChange={(e) => setCidr(e.target.value)}
            placeholder="203.0.113.0/24"
            aria-label="CIDR range"
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm shadow-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-zinc-700 dark:bg-zinc-900"
            required
          />
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="self hosted ingress (optional)"
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
            {creating ? "Adding" : "Add proxy"}
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
        <h2 className="text-sm font-medium">Workspace proxies</h2>

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
                ? "Admin role required to manage trusted proxies."
                : state.status === 401
                ? "Sign in with an admin API key to manage trusted proxies."
                : `Could not load trusted proxies (${state.status || "network"}).`}
            </div>
            <div className="mt-1 break-words text-xs">{state.message}</div>
          </div>
        ) : null}

        {state.kind === "ready" && state.data.workspace_rules.length === 0 ? (
          <div className="mt-3 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/40">
            No workspace proxies. Only the deployment global list is
            consulted.
          </div>
        ) : null}

        {state.kind === "ready" && state.data.workspace_rules.length > 0 ? (
          <ul className="mt-3 divide-y divide-zinc-200 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950">
            {state.data.workspace_rules.map((r) => (
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
        Resolution walks <code className="font-mono">X-Forwarded-For</code> right
        to left and stops at the first hop that is not in the trusted
        set, matching how nginx <code className="font-mono">set_real_ip_from</code> and
        Express <code className="font-mono">trust proxy</code> behave. Adding too
        broad a CIDR lets the entries to its left be spoofed; keep the
        list tight.
      </p>
    </main>
  );
}
