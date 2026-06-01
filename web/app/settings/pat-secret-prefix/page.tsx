"use client";

/**
 * Per-workspace PAT secret prefix policy administration.
 *
 * Workspace owners set a short, custom prefix (e.g. ``acme``); every
 * PAT minted or rotated after that point is shaped as
 * ``pat_<prefix>_<random>`` so the workspace's secret scanner can
 * attribute leaks to the right tenant. Existing PATs are left alone
 * because rewriting their secret value would break live deployments.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  Fingerprint,
  Copy,
  Check,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PrefixResp {
  enforcing: boolean;
  prefix: string;
  example_secret: string;
  scanner_regex: string;
  updated_at: number;
  updated_by: string;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: PrefixResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

export default function PatSecretPrefixPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"regex" | "example" | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/pat-secret-prefix", {
        headers: authHeaders(),
      });
      if (!r.ok) {
        const body = await r.text();
        setState({
          kind: "error",
          status: r.status,
          message: body || r.statusText,
        });
        return;
      }
      const data = (await r.json()) as PrefixResp;
      setState({ kind: "ready", data });
      setDraft(data.prefix);
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

  async function onSave(nextPrefix: string) {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/pat-secret-prefix", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ prefix: nextPrefix }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : body.detail?.message || `Request failed (${r.status})`;
        setSaveError(detail);
        return;
      }
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function copy(kind: "regex" | "example", text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      // clipboard denied; silently noop, UI still shows the value
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
        <span className="text-zinc-200">pat secret prefix</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Fingerprint size={26} weight="duotone" className="text-indigo-300" />
          PAT secret prefix
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Set a short, custom prefix so every newly minted personal
          access token in this workspace is shaped <code>pat_&lt;prefix&gt;_…</code>.
          Your secret scanner can then attribute a leaked token to this
          workspace without false-paging every other clawhum customer.
          Existing tokens are not rewritten so live deployments keep
          working; rotate a token to give it the new shape.
        </p>
      </header>

      {state.kind === "loading" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
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
            <div className="font-medium">Could not load prefix policy</div>
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
              <ShieldCheck size={16} weight="duotone" /> Workspace prefix
            </h2>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                state.data.enforcing
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {state.data.enforcing ? "enforcing" : "no custom prefix"}
            </span>
          </header>

          <div className="space-y-4 px-5 py-4">
            <div>
              <label
                htmlFor="prefix"
                className="block text-xs uppercase tracking-wide text-zinc-500"
              >
                prefix
              </label>
              <div className="mt-1 flex items-center gap-2">
                <span className="font-mono text-sm text-zinc-500">pat_</span>
                <input
                  id="prefix"
                  type="text"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="acme"
                  maxLength={16}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  className="w-44 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 font-mono text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <span className="font-mono text-sm text-zinc-500">
                  _&lt;random&gt;
                </span>
              </div>
              <p className="mt-2 text-[11px] text-zinc-500">
                lower-case [a-z 0-9 -], 2 to 16 chars, no underscore, no
                leading or trailing dash. leave empty to clear.
              </p>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] uppercase tracking-wide text-zinc-500">
                    example secret
                  </span>
                  <button
                    type="button"
                    onClick={() => copy("example", state.data.example_secret)}
                    aria-label="copy example secret"
                    className="text-zinc-400 hover:text-zinc-100"
                  >
                    {copied === "example" ? (
                      <Check size={14} weight="bold" />
                    ) : (
                      <Copy size={14} weight="duotone" />
                    )}
                  </button>
                </div>
                <code className="mt-1 block break-all font-mono text-[12px] text-zinc-100">
                  {state.data.example_secret}
                </code>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] uppercase tracking-wide text-zinc-500">
                    scanner regex
                  </span>
                  <button
                    type="button"
                    onClick={() => copy("regex", state.data.scanner_regex)}
                    aria-label="copy scanner regex"
                    className="text-zinc-400 hover:text-zinc-100"
                  >
                    {copied === "regex" ? (
                      <Check size={14} weight="bold" />
                    ) : (
                      <Copy size={14} weight="duotone" />
                    )}
                  </button>
                </div>
                <code className="mt-1 block break-all font-mono text-[12px] text-zinc-100">
                  {state.data.scanner_regex}
                </code>
              </div>
            </div>
          </div>

          <footer className="flex flex-col gap-3 border-t border-zinc-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[11px] text-zinc-500">
              last updated {formatTs(state.data.updated_at)}
              {state.data.updated_by ? ` by ${state.data.updated_by}` : ""}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onSave("")}
                disabled={saving || !state.data.enforcing}
                className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
              >
                clear prefix
              </button>
              <button
                type="button"
                onClick={() => onSave(draft.trim())}
                disabled={saving || draft.trim() === state.data.prefix}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ShieldCheck size={14} weight="duotone" />
                {saving ? "saving" : "save prefix"}
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
