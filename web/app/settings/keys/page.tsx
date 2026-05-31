"use client";

/**
 * Personal access tokens.
 *
 * Mints, lists, and revokes tokens scoped to the caller's tenant. The
 * freshly created secret is shown exactly once; afterwards only the
 * last four characters survive in the UI. All requests reuse the
 * existing X-API-Key auth via the fetch patch in lib/apiKey.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Key,
  Plus,
  Copy,
  Check,
  Trash,
  Warning,
  ShieldCheck,
  Terminal,
  ArrowLeft,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface KeyRow {
  id: string;
  name: string;
  roles: string[];
  rpm: number;
  created_at: number;
  last_used_at: number;
  secret_hint: string;
}

interface CreatedKey extends KeyRow {
  secret: string;
}

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; rows: KeyRow[] }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function timeAgo(ts: number): string {
  if (!ts) return "never";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export default function KeysPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [justCreated, setJustCreated] = useState<CreatedKey | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [copied, setCopied] = useState<"secret" | "curl" | null>(null);
  const [origin, setOrigin] = useState("http://127.0.0.1:7452");

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/keys", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => r.statusText);
        setState({ kind: "error", status: r.status, message: msg || r.statusText });
        return;
      }
      const rows = (await r.json()) as KeyRow[];
      setState({ kind: "ready", rows });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/keys", {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => r.statusText);
        setCreateError(msg || `failed with ${r.status}`);
        return;
      }
      const created = (await r.json()) as CreatedKey;
      setJustCreated(created);
      setName("");
      await refresh();
    } catch (e: any) {
      setCreateError(e?.message || String(e));
    } finally {
      setCreating(false);
    }
  }

  async function revoke(id: string) {
    setRevoking(id);
    try {
      const r = await fetch(`/api/keys/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (r.ok) {
        setConfirmId(null);
        await refresh();
      }
    } finally {
      setRevoking(null);
    }
  }

  async function copy(label: "secret" | "curl", text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      /* clipboard may be blocked; silent */
    }
  }

  const curl = justCreated
    ? `curl -X POST ${origin}/api/v1/match \\\n  -H "X-API-Key: ${justCreated.secret}" \\\n  -F "audio=@hum.wav"`
    : "";

  return (
    <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8 sm:py-12 space-y-8">
      <header className="space-y-2">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 text-xs font-mono uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft size={12} weight="bold" /> settings
        </Link>
        <div className="flex items-center gap-3">
          <Key size={28} weight="duotone" className="text-[var(--color-accent)]" />
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">
            Personal access tokens
          </h1>
        </div>
        <p className="text-sm text-[var(--color-muted)] max-w-2xl">
          Mint a token to call the ClawHum API from a script, CI job, or third
          party tool. Each token is scoped to your tenant and can be revoked at
          any time. The secret is shown exactly once.
        </p>
      </header>

      {/* Just-created banner */}
      {justCreated && (
        <section
          role="alert"
          className="rounded-lg border border-[var(--color-accent)] bg-[color-mix(in_srgb,var(--color-accent)_8%,var(--color-bg))] p-4 sm:p-5 space-y-3"
        >
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck size={18} weight="duotone" />
            Copy this token now. You will not see it again.
          </div>
          <div className="flex items-center gap-2 bg-[var(--color-bg)] border border-[var(--color-line)] rounded p-2 font-mono text-xs overflow-x-auto">
            <span className="select-all break-all">{justCreated.secret}</span>
            <button
              onClick={() => copy("secret", justCreated.secret)}
              className="ml-auto shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded hover:bg-[var(--color-panel)]"
              aria-label="Copy token"
            >
              {copied === "secret" ? (
                <>
                  <Check size={14} weight="bold" /> copied
                </>
              ) : (
                <>
                  <Copy size={14} weight="bold" /> copy
                </>
              )}
            </button>
          </div>
          <details className="text-xs text-[var(--color-muted)]">
            <summary className="cursor-pointer flex items-center gap-1 select-none">
              <Terminal size={12} weight="bold" /> sample curl
            </summary>
            <div className="mt-2 flex items-start gap-2 bg-[var(--color-bg)] border border-[var(--color-line)] rounded p-2 font-mono text-[11px] overflow-x-auto">
              <pre className="whitespace-pre">{curl}</pre>
              <button
                onClick={() => copy("curl", curl)}
                className="ml-auto shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded hover:bg-[var(--color-panel)]"
                aria-label="Copy curl"
              >
                {copied === "curl" ? (
                  <Check size={14} weight="bold" />
                ) : (
                  <Copy size={14} weight="bold" />
                )}
              </button>
            </div>
          </details>
          <button
            onClick={() => setJustCreated(null)}
            className="text-xs underline text-[var(--color-muted)] hover:text-[var(--color-text)]"
          >
            I have saved it, dismiss
          </button>
        </section>
      )}

      {/* Create form */}
      <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-4 sm:p-5">
        <h2 className="text-sm font-medium mb-3">Create a token</h2>
        <form onSubmit={create} className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="ci-bot, local-laptop, zapier..."
            maxLength={64}
            className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)]"
            aria-label="Token name"
          />
          <button
            type="submit"
            disabled={!name.trim() || creating}
            className="inline-flex items-center justify-center gap-1 px-4 py-2 rounded bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90"
          >
            <Plus size={14} weight="bold" />
            {creating ? "creating..." : "create"}
          </button>
        </form>
        {createError && (
          <p className="mt-2 text-xs text-red-500 flex items-center gap-1">
            <Warning size={12} weight="bold" /> {createError}
          </p>
        )}
      </section>

      {/* Token list */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium">Your tokens</h2>

        {state.kind === "loading" && (
          <div className="space-y-2" aria-busy="true">
            {[0, 1].map((i) => (
              <div
                key={i}
                className="h-16 rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] animate-pulse"
              />
            ))}
          </div>
        )}

        {state.kind === "error" && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium">
              <Warning size={16} weight="duotone" />
              Could not load tokens
            </div>
            <p className="mt-1 text-[var(--color-muted)] text-xs">
              {state.status === 401
                ? "Set your API key in settings first."
                : state.status === 403
                  ? "Your current key cannot manage tokens. You need the writer role."
                  : state.message}
            </p>
            {state.status === 401 && (
              <Link
                href="/settings"
                className="mt-2 inline-block text-xs underline"
              >
                go to settings
              </Link>
            )}
          </div>
        )}

        {state.kind === "ready" && state.rows.length === 0 && (
          <div className="rounded-lg border border-dashed border-[var(--color-line)] p-8 text-center text-sm text-[var(--color-muted)]">
            <Key
              size={32}
              weight="duotone"
              className="mx-auto mb-2 text-[var(--color-muted)]"
            />
            No tokens yet. Mint one above to call the API from outside the
            browser.
          </div>
        )}

        {state.kind === "ready" &&
          state.rows.length > 0 &&
          state.rows
            .slice()
            .sort((a, b) => b.created_at - a.created_at)
            .map((row) => (
              <div
                key={row.id}
                className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-4 flex flex-col sm:flex-row sm:items-center gap-3"
              >
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sm truncate">{row.name}</span>
                    <span className="font-mono text-[11px] text-[var(--color-muted)]">
                      pat_...{row.secret_hint}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--color-muted)] font-mono uppercase tracking-wider">
                    <span>roles: {row.roles.join(", ") || "reader"}</span>
                    <span>used: {timeAgo(row.last_used_at)}</span>
                    <span>created: {timeAgo(row.created_at)}</span>
                  </div>
                </div>
                {confirmId === row.id ? (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => revoke(row.id)}
                      disabled={revoking === row.id}
                      className="px-3 py-1.5 rounded bg-red-500 text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
                    >
                      {revoking === row.id ? "revoking..." : "confirm revoke"}
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      className="px-3 py-1.5 rounded border border-[var(--color-line)] text-xs"
                    >
                      cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmId(row.id)}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded border border-[var(--color-line)] text-xs hover:bg-[var(--color-bg)]"
                    aria-label={`Revoke ${row.name}`}
                  >
                    <Trash size={12} weight="bold" /> revoke
                  </button>
                )}
              </div>
            ))}
      </section>
    </main>
  );
}
