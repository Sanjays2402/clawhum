"use client";

/**
 * Per-workspace PAT scope policy administration.
 *
 * Admins pin the maximum scope set that PATs in this workspace may
 * ever be minted with. An empty list means no restriction so admins
 * keep being able to mint the full role-allowed set.
 *
 * Enforcement: every POST /keys mint clamps requested scopes to
 * (caller_role_scopes ∩ workspace_policy_scopes), and rejects
 * explicit out-of-policy scopes with HTTP 403.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  LockKey,
  Key,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  enforcing: boolean;
  scopes: string[];
  available_scopes: string[];
  updated_at: number;
  updated_by: string;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: PolicyResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

export default function ScopePolicyPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/scope-policy", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({
          kind: "error",
          status: r.status,
          message: body || r.statusText,
        });
        return;
      }
      const data = (await r.json()) as PolicyResp;
      setState({ kind: "ready", data });
      setSelected(new Set(data.scopes));
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

  function toggle(scope: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  async function onSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/scope-policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ scopes: Array.from(selected) }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail || {});
        setSaveError(detail || `Request failed (${r.status})`);
        return;
      }
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function onClear() {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/scope-policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ scopes: [] }),
      });
      if (!r.ok) {
        setSaveError(`Request failed (${r.status})`);
        return;
      }
      await refresh();
    } finally {
      setSaving(false);
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
        <span className="text-zinc-200">scope policy</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <ShieldCheck size={26} weight="duotone" className="text-indigo-300" />
          PAT scope policy
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Pin the maximum scope set this workspace may ever mint on a
          personal access token. Applies to every PAT mint, every actor,
          every role. An empty selection means no restriction so admins
          can mint the full role-allowed set.
        </p>
      </header>

      {state.kind === "loading" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <div className="space-y-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="h-5 w-2/3 animate-pulse rounded bg-zinc-800"
              />
            ))}
          </div>
        </div>
      )}

      {state.kind === "error" && (
        <div className="flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/5 px-5 py-6 text-sm text-rose-300">
          <Warning size={18} weight="duotone" />
          <div>
            <div className="font-medium">Could not load scope policy</div>
            <div className="mt-1 text-xs text-rose-200/70">
              {state.status ? `HTTP ${state.status} ` : ""}
              {state.message}
            </div>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60">
          <header className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
            <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <Key size={16} weight="duotone" /> Allowed scopes
            </h2>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                state.data.enforcing
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {state.data.enforcing ? "enforcing" : "no restriction"}
            </span>
          </header>

          <ul className="divide-y divide-zinc-900">
            {state.data.available_scopes.map((scope) => {
              const checked = selected.has(scope);
              return (
                <li key={scope}>
                  <label className="flex cursor-pointer items-center justify-between px-5 py-3 hover:bg-zinc-900/40">
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(scope)}
                        className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 text-indigo-500 focus:ring-indigo-500"
                      />
                      <code className="text-sm text-zinc-100">{scope}</code>
                    </div>
                    {scope === "admin" || scope === "write:keys" ? (
                      <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-rose-300">
                        sensitive
                      </span>
                    ) : null}
                  </label>
                </li>
              );
            })}
          </ul>

          <footer className="flex flex-col gap-3 border-t border-zinc-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[11px] text-zinc-500">
              last updated {formatTs(state.data.updated_at)}
              {state.data.updated_by ? ` by ${state.data.updated_by}` : ""}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClear}
                disabled={saving || !state.data.enforcing}
                className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
              >
                clear policy
              </button>
              <button
                type="button"
                onClick={onSave}
                disabled={saving}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <LockKey size={14} weight="duotone" />
                {saving ? "saving" : selected.size === 0 ? "save (no restriction)" : "save policy"}
              </button>
            </div>
          </footer>
          {saveError && (
            <p className="flex items-center gap-1 border-t border-zinc-800 px-5 py-3 text-xs text-rose-400">
              <Warning size={14} weight="duotone" /> {saveError}
            </p>
          )}
        </section>
      )}
    </main>
  );
}
