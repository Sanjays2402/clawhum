"use client";

/**
 * Per-workspace allowed authentication methods.
 *
 * Admins pin which credential classes the workspace accepts:
 * env_key (deploy-time static keys), pat (personal access tokens),
 * and scim (IdP bearer tokens). Disabling a method blocks both
 * authentication AND mint where applicable; existing live PATs in a
 * pat-disabled workspace will fail their next request with a
 * deterministic 401.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  LockKey,
  Key,
  IdentificationCard,
  Cloud,
} from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  enforcing: boolean;
  methods: string[];
  available_methods: string[];
  effective_methods: string[];
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

const METHOD_LABELS: Record<string, { title: string; blurb: string }> = {
  env_key: {
    title: "Deploy-time API keys",
    blurb:
      "Static keys configured by ops via CLAWHUM_API_KEYS. Disable to require every credential to be minted by a named workspace admin.",
  },
  pat: {
    title: "Personal access tokens",
    blurb:
      "Self-serve tokens minted from this dashboard. Disable to force every machine actor through a SCIM-provisioned account.",
  },
  scim: {
    title: "SCIM bearer tokens",
    blurb:
      "IdP-issued tokens used by Okta, Azure AD, and Google Workspace to push user lifecycle events.",
  },
};

const METHOD_ICONS: Record<string, Icon> = {
  env_key: Key,
  pat: IdentificationCard,
  scim: Cloud,
};

export default function AuthMethodsPolicyPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/auth-methods-policy", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as PolicyResp;
      setState({ kind: "ready", data });
      const seed = data.enforcing ? data.methods : data.effective_methods;
      setSelected(new Set(seed));
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

  function toggle(method: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(method)) next.delete(method);
      else next.add(method);
      return next;
    });
  }

  async function onSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/auth-methods-policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ methods: Array.from(selected) }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || {});
        setSaveError(detail || `Request failed (${r.status})`);
        return;
      }
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  const empty = selected.size === 0;

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 text-zinc-100">
      <div className="mb-6 flex items-center gap-3 text-sm text-zinc-400">
        <Link href="/settings" className="inline-flex items-center gap-1 hover:text-zinc-200">
          <ArrowLeft size={16} weight="duotone" /> settings
        </Link>
        <span>/</span>
        <span className="text-zinc-200">auth methods</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <ShieldCheck size={26} weight="duotone" className="text-indigo-300" />
          Allowed authentication methods
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Pick which credential classes this workspace accepts. A method that is
          unchecked is rejected at the auth layer, and PATs cannot be minted when
          the PAT method is disabled. Leave every box checked for no restriction.
        </p>
      </header>

      {state.kind === "loading" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-12 w-full animate-pulse rounded bg-zinc-800" />
            ))}
          </div>
        </div>
      )}

      {state.kind === "error" && (
        <div className="flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/5 px-5 py-6 text-sm text-rose-300">
          <Warning size={18} weight="duotone" />
          <div>
            <div className="font-medium">Could not load policy</div>
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
              <LockKey size={16} weight="duotone" /> Methods
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
            {state.data.available_methods.map((m) => {
              const checked = selected.has(m);
              const meta = METHOD_LABELS[m] ?? { title: m, blurb: "" };
              const Icon = METHOD_ICONS[m] ?? Key;
              return (
                <li key={m}>
                  <label className="flex cursor-pointer items-start gap-3 px-5 py-4 hover:bg-zinc-900/40">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(m)}
                      className="mt-1 h-4 w-4 rounded border-zinc-700 bg-zinc-900 text-indigo-500 focus:ring-indigo-500"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Icon size={16} weight="duotone" />
                        <span className="text-sm font-medium text-zinc-100">{meta.title}</span>
                        <code className="rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] text-zinc-400">
                          {m}
                        </code>
                      </div>
                      <p className="mt-1 text-xs text-zinc-400">{meta.blurb}</p>
                    </div>
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
            <button
              type="button"
              onClick={onSave}
              disabled={saving || empty}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <LockKey size={14} weight="duotone" />
              {saving ? "saving" : "save policy"}
            </button>
          </footer>

          {empty && (
            <p className="border-t border-zinc-800 px-5 py-3 text-xs text-amber-300">
              Pick at least one method. An empty set would lock every caller out.
            </p>
          )}

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
