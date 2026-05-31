"use client";

/**
 * Workspace closure / wind-down administration.
 *
 * An admin schedules the workspace for closure with a grace window.
 * During the grace window the backend rejects every mutating request
 * for this tenant with HTTP 423 Locked so customer data is preserved
 * read-only while the customer exports. After the deadline elapses
 * the workspace is closed: non-export reads return HTTP 410 Gone.
 * The closure can be cancelled by an admin any time before the
 * deadline. The closure log is append-only and emitted to audit.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Warning,
  CheckCircle,
  LockKey,
  Power,
  XCircle,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface ClosureOut {
  id: string;
  tenant_id: string;
  reason: string;
  scheduled_at: number;
  scheduled_by: string;
  finalize_at: number;
  cancelled_at: number | null;
  cancelled_by: string | null;
  state: "scheduled" | "closed" | "cancelled";
}

interface StatusOut {
  tenant_id: string;
  state: "active" | "scheduled" | "closed";
  closure: ClosureOut | null;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: StatusOut }
  | { kind: "error"; status: number; message: string };

const GRACE_PRESETS = [
  { label: "1 hour", seconds: 3600 },
  { label: "24 hours", seconds: 24 * 3600 },
  { label: "7 days", seconds: 7 * 24 * 3600 },
  { label: "30 days", seconds: 30 * 24 * 3600 },
];

function authHeaders(mfa?: string): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  if (mfa) h["X-MFA-Code"] = mfa;
  return h;
}

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function countdown(seconds: number): string {
  if (seconds <= 0) return "elapsed";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function WorkspaceClosurePage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [reason, setReason] = useState("");
  const [graceSeconds, setGraceSeconds] = useState<number>(7 * 24 * 3600);
  const [mfa, setMfa] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [now, setNow] = useState<number>(() => Date.now() / 1000);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/workspace/closure", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as StatusOut;
      setState({ kind: "ready", data });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const closure = state.kind === "ready" ? state.data.closure : null;
  const currentState = state.kind === "ready" ? state.data.state : null;
  const remaining = useMemo(() => {
    if (!closure || closure.state !== "scheduled") return 0;
    return Math.max(0, closure.finalize_at - now);
  }, [closure, now]);

  async function schedule() {
    setActionError(null);
    setFlash(null);
    if (!reason.trim()) {
      setActionError("Reason is required");
      return;
    }
    if (confirmText.trim().toLowerCase() !== "close workspace") {
      setActionError("Type 'close workspace' to confirm");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/workspace/closure", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(mfa) },
        body: JSON.stringify({ reason: reason.trim(), grace_seconds: graceSeconds }),
      });
      if (!r.ok) {
        const text = await r.text();
        setActionError(`${r.status} ${text || r.statusText}`);
        return;
      }
      setFlash("Closure scheduled. The workspace is now read-only.");
      setReason("");
      setConfirmText("");
      setMfa("");
      await refresh();
    } catch (e: any) {
      setActionError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!closure) return;
    setActionError(null);
    setFlash(null);
    setBusy(true);
    try {
      const r = await fetch(`/api/workspace/closure/${closure.id}/cancel`, {
        method: "POST",
        headers: authHeaders(mfa),
      });
      if (!r.ok) {
        const text = await r.text();
        setActionError(`${r.status} ${text || r.statusText}`);
        return;
      }
      setFlash("Closure cancelled. Mutations resume immediately.");
      setMfa("");
      await refresh();
    } catch (e: any) {
      setActionError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
      >
        <ArrowLeft size={12} weight="duotone" />
        settings
      </Link>

      <header className="mt-4 mb-6 space-y-2">
        <h1 className="font-mono text-lg uppercase tracking-widest text-[var(--color-phosphor)]">
          workspace closure
        </h1>
        <p className="font-mono text-[11px] text-[var(--color-dim)] leading-relaxed">
          schedule a wind-down for this workspace. during the grace window every mutating request returns HTTP 423 so customer data is preserved for export. once the deadline elapses non-export reads return HTTP 410. cancel any time before the deadline. admin role plus MFA required.
        </p>
      </header>

      {state.kind === "loading" && (
        <div className="panel rounded-[2px] p-6">
          <div className="h-3 w-32 animate-pulse bg-[var(--color-line)]" />
          <div className="mt-3 h-3 w-48 animate-pulse bg-[var(--color-line)]" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="panel rounded-[2px] border-red-700 p-4 font-mono text-[11px] text-red-400">
          <div className="flex items-center gap-2">
            <Warning size={14} weight="duotone" />
            <span>failed to load closure status ({state.status})</span>
          </div>
          <pre className="mt-2 whitespace-pre-wrap break-all text-[10px] text-[var(--color-dim)]">
            {state.message}
          </pre>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <section className="panel rounded-[2px] p-4">
            <div className="flex items-center gap-2">
              {currentState === "active" && (
                <>
                  <CheckCircle size={16} weight="duotone" className="text-emerald-400" />
                  <span className="font-mono text-[11px] uppercase tracking-widest text-emerald-400">
                    active
                  </span>
                </>
              )}
              {currentState === "scheduled" && (
                <>
                  <LockKey size={16} weight="duotone" className="text-amber-400" />
                  <span className="font-mono text-[11px] uppercase tracking-widest text-amber-400">
                    scheduled for closure
                  </span>
                </>
              )}
              {currentState === "closed" && (
                <>
                  <XCircle size={16} weight="duotone" className="text-red-400" />
                  <span className="font-mono text-[11px] uppercase tracking-widest text-red-400">
                    closed
                  </span>
                </>
              )}
              <span className="ml-auto font-mono text-[10px] text-[var(--color-dim)]">
                tenant {state.data.tenant_id}
              </span>
            </div>

            {closure && (
              <dl className="mt-4 grid grid-cols-1 gap-3 font-mono text-[11px] sm:grid-cols-2">
                <div>
                  <dt className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">reason</dt>
                  <dd className="text-[var(--color-phosphor)] break-words">{closure.reason}</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">scheduled by</dt>
                  <dd className="text-[var(--color-phosphor)] break-all">{closure.scheduled_by || "unknown"}</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">scheduled at</dt>
                  <dd className="text-[var(--color-muted)]">{formatTs(closure.scheduled_at)}</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">finalize at</dt>
                  <dd className="text-[var(--color-muted)]">{formatTs(closure.finalize_at)}</dd>
                </div>
                {closure.state === "scheduled" && (
                  <div className="sm:col-span-2">
                    <dt className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">time remaining</dt>
                    <dd className="text-amber-400">{countdown(remaining)}</dd>
                  </div>
                )}
              </dl>
            )}
          </section>

          {currentState === "scheduled" && (
            <section className="panel mt-4 rounded-[2px] p-4 space-y-3">
              <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)]">
                cancel closure
              </div>
              <p className="font-mono text-[10px] text-[var(--color-dim)]">
                cancelling restores write access immediately. the scheduled record stays in the audit log.
              </p>
              <label className="block font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                MFA code (if enrolled)
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={mfa}
                  onChange={(e) => setMfa(e.target.value)}
                  className="mt-1 block w-32 border border-[var(--color-line)] bg-transparent px-2 py-1 font-mono text-[12px] text-[var(--color-phosphor)] focus:outline-none focus:border-[var(--color-phosphor)]"
                  placeholder="000000"
                />
              </label>
              <button
                type="button"
                onClick={cancel}
                disabled={busy}
                className="inline-flex items-center gap-2 border border-emerald-700 px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-emerald-400 hover:bg-emerald-900/30 disabled:opacity-50"
              >
                <CheckCircle size={12} weight="duotone" />
                {busy ? "cancelling..." : "cancel closure"}
              </button>
            </section>
          )}

          {currentState === "active" && (
            <section className="panel mt-4 rounded-[2px] p-4 space-y-3">
              <div className="font-mono text-[11px] uppercase tracking-widest text-red-400">
                schedule closure
              </div>
              <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
                this freezes writes across the workspace for the grace window, then permanently closes it. export any data you need before the deadline. once closed, this cannot be undone via API.
              </p>

              <label className="block font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                reason
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={1024}
                  className="mt-1 block w-full border border-[var(--color-line)] bg-transparent px-2 py-1 font-mono text-[12px] text-[var(--color-phosphor)] focus:outline-none focus:border-[var(--color-phosphor)]"
                  placeholder="contract not renewed; migrating to internal system"
                />
              </label>

              <div>
                <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">grace window</div>
                <div className="mt-1 flex flex-wrap gap-2">
                  {GRACE_PRESETS.map((p) => (
                    <button
                      type="button"
                      key={p.seconds}
                      onClick={() => setGraceSeconds(p.seconds)}
                      className={`border px-2 py-1 font-mono text-[11px] uppercase tracking-widest ${
                        graceSeconds === p.seconds
                          ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)]"
                          : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              <label className="block font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                MFA code (if enrolled)
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={mfa}
                  onChange={(e) => setMfa(e.target.value)}
                  className="mt-1 block w-32 border border-[var(--color-line)] bg-transparent px-2 py-1 font-mono text-[12px] text-[var(--color-phosphor)] focus:outline-none focus:border-[var(--color-phosphor)]"
                  placeholder="000000"
                />
              </label>

              <label className="block font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                type &lsquo;close workspace&rsquo; to confirm
                <input
                  type="text"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  className="mt-1 block w-full border border-[var(--color-line)] bg-transparent px-2 py-1 font-mono text-[12px] text-red-400 focus:outline-none focus:border-red-400"
                  placeholder="close workspace"
                />
              </label>

              <button
                type="button"
                onClick={schedule}
                disabled={busy || !reason.trim() || confirmText.trim().toLowerCase() !== "close workspace"}
                className="inline-flex items-center gap-2 border border-red-700 px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-red-400 hover:bg-red-900/30 disabled:opacity-50"
              >
                <Power size={12} weight="duotone" />
                {busy ? "scheduling..." : "schedule closure"}
              </button>
            </section>
          )}

          {currentState === "closed" && (
            <section className="panel mt-4 rounded-[2px] border-red-700 p-4 font-mono text-[11px] text-red-400">
              this workspace is closed. all non-export endpoints return HTTP 410. contact support to reopen.
            </section>
          )}

          {(actionError || flash) && (
            <div
              className={`mt-4 panel rounded-[2px] p-3 font-mono text-[11px] ${
                actionError ? "border-red-700 text-red-400" : "border-emerald-700 text-emerald-400"
              }`}
            >
              {actionError || flash}
            </div>
          )}
        </>
      )}
    </main>
  );
}
