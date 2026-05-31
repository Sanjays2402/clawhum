"use client";

/**
 * Workspace invite email-domain allowlist administration.
 *
 * Admins pin which email domains can hold a seat in this workspace.
 * An empty list means no restriction so existing tenants keep working.
 *
 * Enforcement points wired into the same allowlist:
 *  - POST /members/invite rejects out-of-policy emails with HTTP 422
 *  - POST /members/accept re-checks at acceptance so a tightened
 *    policy still applies to in-flight invites
 *  - SSO auto-join and SCIM-side provisioning route through the same
 *    member_store so there is no bypass path.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  EnvelopeSimple,
  Plus,
  Trash,
  Warning,
  ArrowLeft,
  ShieldCheck,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface DomainRow {
  id: string;
  domain: string;
  include_subdomains: boolean;
  label: string;
  created_at: number;
}

interface ListResp {
  enforcing: boolean;
  domains: DomainRow[];
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

export default function InviteDomainsPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [domain, setDomain] = useState("");
  const [label, setLabel] = useState("");
  const [includeSubdomains, setIncludeSubdomains] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/invite-domains", { headers: authHeaders() });
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
    if (!domain.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/invite-domains", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          domain: domain.trim(),
          include_subdomains: includeSubdomains,
          label: label.trim(),
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail = typeof body.detail === "string" ? body.detail : null;
        setCreateError(detail || `Request failed (${r.status})`);
        return;
      }
      setDomain("");
      setLabel("");
      setIncludeSubdomains(false);
      await refresh();
    } finally {
      setCreating(false);
    }
  }

  async function onDelete(id: string) {
    setDeleting(id);
    try {
      const r = await fetch(`/api/invite-domains/${encodeURIComponent(id)}`, {
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
        <span className="text-zinc-200">invite domains</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <EnvelopeSimple size={26} weight="duotone" className="text-indigo-300" />
          Invite domain allowlist
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Pin which email domains can hold a seat in this workspace.
          Applies to manual invites, SSO auto-join, and SCIM provisioning.
          Empty list means no restriction so existing members keep working.
        </p>
      </header>

      <section
        aria-labelledby="add-domain"
        className="mb-8 rounded-xl border border-zinc-800 bg-zinc-950/60 p-5"
      >
        <h2
          id="add-domain"
          className="mb-4 flex items-center gap-2 text-sm font-medium text-zinc-200"
        >
          <Plus size={16} weight="duotone" /> Add a domain
        </h2>
        <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-[1fr,200px,auto]">
          <input
            type="text"
            required
            placeholder="acme.com"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-indigo-500 focus:outline-none"
            aria-label="email domain"
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
            disabled={creating || !domain.trim()}
            className="inline-flex items-center justify-center gap-1 rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <LockKey size={16} weight="duotone" />
            {creating ? "adding" : "add"}
          </button>
        </form>
        <label className="mt-3 inline-flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={includeSubdomains}
            onChange={(e) => setIncludeSubdomains(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-zinc-700 bg-zinc-900 text-indigo-500 focus:ring-indigo-500"
          />
          Include subdomains (alice@eu.acme.com matches acme.com)
        </label>
        {createError && (
          <p className="mt-3 flex items-center gap-1 text-xs text-rose-400">
            <Warning size={14} weight="duotone" /> {createError}
          </p>
        )}
        <p className="mt-3 text-xs text-zinc-500">
          Bare domain only, no leading @. Example:
          <code className="ml-1 rounded bg-zinc-900 px-1.5 py-0.5 text-[11px] text-zinc-300">
            acme.com
          </code>
        </p>
      </section>

      <section
        aria-labelledby="rules"
        className="rounded-xl border border-zinc-800 bg-zinc-950/60"
      >
        <header className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <h2
            id="rules"
            className="flex items-center gap-2 text-sm font-medium text-zinc-200"
          >
            <ShieldCheck size={16} weight="duotone" /> Allowed domains
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
              <li
                key={i}
                className="flex items-center justify-between px-5 py-4"
              >
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
              <div className="font-medium">Could not load invite domains</div>
              <div className="mt-1 text-xs text-rose-200/70">
                {state.status ? `HTTP ${state.status} ` : ""}
                {state.message}
              </div>
            </div>
          </div>
        )}

        {state.kind === "ready" && state.data.domains.length === 0 && (
          <div className="px-5 py-10 text-center text-sm text-zinc-400">
            <EnvelopeSimple
              size={28}
              weight="duotone"
              className="mx-auto mb-2 text-zinc-600"
            />
            No domains yet. Invites accept any email address.
          </div>
        )}

        {state.kind === "ready" && state.data.domains.length > 0 && (
          <ul className="divide-y divide-zinc-900">
            {state.data.domains.map((d) => (
              <li
                key={d.id}
                className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <code className="truncate text-zinc-100">
                      {d.include_subdomains ? `*.${d.domain}` : `@${d.domain}`}
                    </code>
                    {d.include_subdomains && (
                      <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-indigo-300">
                        subdomains
                      </span>
                    )}
                    {d.label && (
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400">
                        {d.label}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-zinc-500">
                    added {formatTs(d.created_at)}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {confirmId === d.id ? (
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
                        onClick={() => onDelete(d.id)}
                        disabled={deleting === d.id}
                        className="inline-flex items-center gap-1 rounded-md bg-rose-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-400 disabled:opacity-50"
                      >
                        <Trash size={14} weight="duotone" />
                        {deleting === d.id ? "removing" : "confirm remove"}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmId(d.id)}
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
