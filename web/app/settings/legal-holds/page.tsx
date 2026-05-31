"use client";

/**
 * Workspace legal hold (litigation hold) administration.
 *
 * When a hold is active, the backend rejects every destructive
 * operation for this tenant with HTTP 423 Locked: retention purge
 * sweeps, GDPR /privacy/me erasure, and history record deletion.
 * Reads, exports, and audit appends are never blocked, so the
 * workspace stays usable for compliance review while data is frozen
 * for preservation. Releasing a hold is recorded in the timeline
 * and emitted to the audit log; the hold record itself is never
 * removed.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  CheckCircle,
  LockKey,
  Snowflake,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface HoldOut {
  id: string;
  tenant_id: string;
  reason: string;
  created_at: number;
  created_by: string;
  released_at: number | null;
  released_by: string | null;
  active: boolean;
}

interface HoldListOut {
  tenant_id: string;
  on_hold: boolean;
  active_hold_id: string | null;
  holds: HoldOut[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: HoldListOut }
  | { kind: "error"; status: number; message: string };

function authHeaders(mfa?: string): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  if (mfa) h["X-MFA-Code"] = mfa;
  return h;
}

function formatTs(ts: number | null): string {
  if (!ts) return "\u2014";
  return new Date(ts * 1000).toLocaleString();
}

export default function LegalHoldsPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [reason, setReason] = useState("");
  const [mfa, setMfa] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/legal-holds", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as HoldListOut;
      setState({ kind: "ready", data });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function placeHold() {
    setActionError(null);
    setFlash(null);
    if (!reason.trim()) {
      setActionError("Reason is required");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/legal-holds", {
        method: "POST",
        headers: { ...authHeaders(mfa), "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      if (!r.ok) {
        const body = await r.text();
        setActionError(`${r.status}: ${body || r.statusText}`);
        return;
      }
      setReason("");
      setMfa("");
      setFlash("Legal hold placed. Destructive operations are frozen.");
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function releaseHold(id: string) {
    setActionError(null);
    setFlash(null);
    setBusy(true);
    try {
      const r = await fetch(`/api/legal-holds/${encodeURIComponent(id)}/release`, {
        method: "POST",
        headers: authHeaders(mfa),
      });
      if (!r.ok) {
        const body = await r.text();
        setActionError(`${r.status}: ${body || r.statusText}`);
        return;
      }
      setMfa("");
      setFlash("Hold released. Destructive operations are restored.");
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <div className="mb-6 flex items-center gap-2 text-sm text-zinc-500">
        <Link href="/settings" className="inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100">
          <ArrowLeft weight="duotone" className="h-4 w-4" />
          Settings
        </Link>
      </div>

      <div className="mb-8 flex items-start gap-3">
        <Snowflake weight="duotone" className="mt-1 h-7 w-7 text-sky-500" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Legal hold
          </h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            Freeze destructive operations on this workspace for litigation,
            investigation, or regulatory preservation. Retention purges,
            GDPR erasures, and history deletes return HTTP 423 while a
            hold is active. Reads and exports keep working.
          </p>
        </div>
      </div>

      {state.kind === "loading" && (
        <div className="rounded-lg border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950">
          Loading legal hold status...
        </div>
      )}

      {state.kind === "error" && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <Warning weight="duotone" className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <div className="font-medium">Could not load holds</div>
            <div className="mt-1 break-words">
              {state.status ? `${state.status}: ` : ""}
              {state.message}
            </div>
            <button
              className="mt-2 rounded-md border border-amber-300 px-3 py-1 text-xs hover:bg-amber-100 dark:border-amber-800 dark:hover:bg-amber-900/40"
              onClick={refresh}
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <div className="space-y-6">
          <div
            className={`rounded-lg border p-4 text-sm ${
              state.data.on_hold
                ? "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200"
                : "border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-300"
            }`}
          >
            <div className="flex items-center gap-2 font-medium">
              {state.data.on_hold ? (
                <>
                  <Snowflake weight="duotone" className="h-5 w-5" />
                  Workspace is under legal hold
                </>
              ) : (
                <>
                  <CheckCircle weight="duotone" className="h-5 w-5" />
                  No active hold
                </>
              )}
            </div>
            {state.data.on_hold && state.data.active_hold_id && (
              <div className="mt-1 font-mono text-xs opacity-80">
                {state.data.active_hold_id}
              </div>
            )}
          </div>

          <section className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              <ShieldCheck weight="duotone" className="h-4 w-4 text-emerald-500" />
              Place a new hold
            </h2>
            <p className="mt-1 text-xs text-zinc-500">
              Admin role and a fresh MFA code are required.
            </p>
            <div className="mt-4 space-y-3">
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Reason
                <textarea
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                  rows={3}
                  placeholder="e.g. Litigation Smith v. Acme, case 2026-CV-0419"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={1024}
                />
              </label>
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                MFA code
                <input
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
                  placeholder="123456"
                  value={mfa}
                  onChange={(e) => setMfa(e.target.value)}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                />
              </label>
              <button
                className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                onClick={placeHold}
                disabled={busy || !reason.trim()}
              >
                <LockKey weight="duotone" className="h-4 w-4" />
                {busy ? "Working..." : "Place hold"}
              </button>
              {actionError && (
                <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-200">
                  {actionError}
                </div>
              )}
              {flash && (
                <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-200">
                  {flash}
                </div>
              )}
            </div>
          </section>

          <section className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
            <div className="border-b border-zinc-200 px-5 py-3 text-sm font-semibold text-zinc-900 dark:border-zinc-800 dark:text-zinc-100">
              Timeline
            </div>
            {state.data.holds.length === 0 ? (
              <div className="px-5 py-10 text-center text-sm text-zinc-500">
                No holds have ever been placed on this workspace.
              </div>
            ) : (
              <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {state.data.holds.map((h) => (
                  <li key={h.id} className="px-5 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                          <span className="font-mono text-xs text-zinc-500">{h.id}</span>
                          {h.active ? (
                            <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-700 dark:bg-sky-900/40 dark:text-sky-200">
                              Active
                            </span>
                          ) : (
                            <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                              Released
                            </span>
                          )}
                        </div>
                        <div className="mt-1 break-words text-sm text-zinc-900 dark:text-zinc-100">
                          {h.reason}
                        </div>
                        <dl className="mt-2 grid grid-cols-1 gap-1 text-xs text-zinc-500 sm:grid-cols-2">
                          <div>
                            <dt className="inline">Placed </dt>
                            <dd className="inline">
                              {formatTs(h.created_at)} by {h.created_by || "\u2014"}
                            </dd>
                          </div>
                          <div>
                            <dt className="inline">Released </dt>
                            <dd className="inline">
                              {h.released_at
                                ? `${formatTs(h.released_at)} by ${h.released_by || "\u2014"}`
                                : "\u2014"}
                            </dd>
                          </div>
                        </dl>
                      </div>
                      {h.active && (
                        <button
                          className="shrink-0 rounded-md border border-zinc-300 px-3 py-1 text-xs font-medium hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
                          onClick={() => releaseHold(h.id)}
                          disabled={busy || !mfa}
                          title={mfa ? "Release hold" : "Enter MFA code above to release"}
                        >
                          Release
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
