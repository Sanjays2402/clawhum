"use client";

/**
 * MFA step-up ("sudo mode") session management.
 *
 * After enrolling MFA, every destructive admin call requires a fresh
 * TOTP code by default. That UX is brutal for admins paging through
 * the console. This page lets a workspace admin:
 *
 *   1. See whether sudo mode is enabled for their workspace and how
 *      long an issued session token lasts.
 *   2. Exchange a fresh TOTP code for a short lived ``X-MFA-Session``
 *      token that stands in for the code on subsequent calls.
 *   3. Revoke every outstanding step-up token in one click, used
 *      after a suspected leak or before stepping away from a shared
 *      machine.
 *
 * The issued token is stored in ``sessionStorage`` only, never in
 * localStorage, so it dies when the tab closes.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  Lock,
  LockOpen,
  ArrowLeft,
  Warning,
  Timer,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface StatusResp {
  enabled: boolean;
  ttl_seconds: number;
  max_ttl_seconds: number;
  epoch: number;
}

interface IssueResp {
  token: string;
  ttl_seconds: number;
  expires_at: number;
}

const STORAGE_KEY = "clawhum.mfa.session";

type Held = { token: string; expires_at: number } | null;

function loadHeld(): Held {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Held;
    if (!v || !v.token || !v.expires_at) return null;
    if (v.expires_at * 1000 <= Date.now()) {
      window.sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return v;
  } catch {
    return null;
  }
}

function saveHeld(v: Held) {
  if (typeof window === "undefined") return;
  if (v) window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(v));
  else window.sessionStorage.removeItem(STORAGE_KEY);
}

function formatRemaining(secs: number): string {
  if (secs <= 0) return "expired";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function SudoSessionPage() {
  useApiKey();
  const [status, setStatus] = useState<StatusResp | null>(null);
  const [held, setHeld] = useState<Held>(null);
  const [now, setNow] = useState<number>(() => Math.floor(Date.now() / 1000));
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const apiKey = getApiKey();
      const r = await fetch("/api/mfa/session", {
        headers: { "X-API-Key": apiKey ?? "" },
      });
      if (!r.ok) {
        setLoadError(`status ${r.status}`);
        return;
      }
      setStatus((await r.json()) as StatusResp);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setHeld(loadHeld());
    refresh();
  }, [refresh]);

  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Math.floor(Date.now() / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (held && held.expires_at <= now) {
      saveHeld(null);
      setHeld(null);
    }
  }, [held, now]);

  const remaining = useMemo(
    () => (held ? Math.max(0, held.expires_at - now) : 0),
    [held, now],
  );

  const onIssue = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setBusy(true);
      setError(null);
      try {
        const apiKey = getApiKey();
        const r = await fetch("/api/mfa/session", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": apiKey ?? "",
          },
          body: JSON.stringify({ code: code.trim() }),
        });
        if (!r.ok) {
          const j = (await r.json().catch(() => ({}))) as { detail?: string };
          setError(j.detail ?? `status ${r.status}`);
          return;
        }
        const j = (await r.json()) as IssueResp;
        const next = { token: j.token, expires_at: j.expires_at };
        saveHeld(next);
        setHeld(next);
        setCode("");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [code],
  );

  const onRevoke = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const apiKey = getApiKey();
      const r = await fetch("/api/mfa/session", {
        method: "DELETE",
        headers: { "X-API-Key": apiKey ?? "" },
      });
      if (!r.ok) {
        const j = (await r.json().catch(() => ({}))) as { detail?: string };
        setError(j.detail ?? `status ${r.status}`);
        return;
      }
      saveHeld(null);
      setHeld(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-8 sm:px-6 sm:py-10">
      <Link
        href="/settings/security"
        className="inline-flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
      >
        <ArrowLeft size={14} weight="duotone" />
        Back to security
      </Link>
      <header className="mt-4 mb-6 flex items-start gap-3">
        <ShieldCheck size={28} weight="duotone" className="mt-0.5 text-zinc-700 dark:text-zinc-300" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Sudo mode</h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            Verify once with your authenticator and unlock destructive admin
            calls for a short window. Closes automatically when the tab does.
          </p>
        </div>
      </header>

      {loadError && (
        <div className="mb-4 flex items-center gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <Warning size={14} weight="duotone" />
          Could not load step-up status: {loadError}
        </div>
      )}

      {status === null && !loadError && (
        <div className="space-y-3">
          <div className="h-20 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
          <div className="h-32 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
        </div>
      )}

      {status && status.enabled === false && (
        <section className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-300">
          <div className="flex items-center gap-2 font-medium">
            <Lock size={16} weight="duotone" />
            Sudo mode is disabled for this workspace
          </div>
          <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
            Every destructive admin call will require a fresh TOTP code.
            Workspace operators can enable a short session window by setting
            <code className="mx-1 rounded bg-zinc-200 px-1 font-mono dark:bg-zinc-800">CLAWHUM_MFA_SESSION_TTL_SECONDS</code>
            to a positive value, capped at
            {" "}{status.max_ttl_seconds}s.
          </p>
        </section>
      )}

      {status && status.enabled && (
        <div className="space-y-6">
          {held ? (
            <section className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-medium text-emerald-900 dark:text-emerald-200">
                  <LockOpen size={16} weight="duotone" />
                  Sudo mode active
                </div>
                <div
                  className="inline-flex items-center gap-1 rounded bg-white/70 px-2 py-0.5 font-mono text-xs text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100"
                  aria-live="polite"
                >
                  <Timer size={12} weight="duotone" />
                  {formatRemaining(remaining)}
                </div>
              </div>
              <p className="mt-2 text-xs text-emerald-900/80 dark:text-emerald-200/80">
                The dashboard will attach this token as
                {" "}<code className="font-mono">X-MFA-Session</code>{" "}
                on destructive calls until it expires.
              </p>
              <button
                type="button"
                onClick={onRevoke}
                disabled={busy}
                className="mt-3 inline-flex items-center gap-1.5 rounded border border-emerald-400 px-3 py-1.5 text-xs font-medium text-emerald-900 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-800 dark:text-emerald-100 dark:hover:bg-emerald-900/40"
              >
                <Lock size={12} weight="duotone" />
                {busy ? "Revoking..." : "Lock now"}
              </button>
            </section>
          ) : (
            <section className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Lock size={16} weight="duotone" />
                Unlock sudo mode
              </div>
              <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                Enter a code from your authenticator or a single-use recovery
                code. The session lasts {status.ttl_seconds}s, capped at
                {" "}{status.max_ttl_seconds}s.
              </p>
              <form onSubmit={onIssue} className="mt-3">
                <label htmlFor="totp" className="text-[10px] uppercase tracking-widest text-zinc-500">
                  TOTP or recovery code
                </label>
                <input
                  id="totp"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="123456 or ABCDE-FGHIJ"
                  autoComplete="one-time-code"
                  inputMode="text"
                  className="mt-1 block w-64 rounded border border-zinc-300 bg-white px-3 py-2 font-mono text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950"
                />
                {error && (
                  <p className="mt-2 text-xs text-red-700 dark:text-red-400">{error}</p>
                )}
                <button
                  type="submit"
                  disabled={busy || code.trim().length < 4}
                  className="mt-3 inline-flex items-center gap-1.5 rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                >
                  <LockOpen size={14} weight="duotone" />
                  {busy ? "Verifying..." : "Unlock"}
                </button>
              </form>
            </section>
          )}

          <section className="rounded-lg border border-zinc-200 p-4 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
            <div className="mb-1 text-[10px] uppercase tracking-widest text-zinc-500">
              How sudo mode works
            </div>
            <ul className="list-inside list-disc space-y-1">
              <li>Token is signed by the server, bound to your actor and workspace.</li>
              <li>Disabling MFA or force-logging-out sessions revokes every outstanding token.</li>
              <li>Storage is sessionStorage only, so the token dies when the tab closes.</li>
              <li>Server cap: {status.max_ttl_seconds} seconds. Current TTL: {status.ttl_seconds} seconds.</li>
            </ul>
          </section>
        </div>
      )}
    </main>
  );
}
