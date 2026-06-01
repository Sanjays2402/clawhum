"use client";

/**
 * Admin view of personal-access-token brute-force lockouts.
 *
 * Lists source IPs whose failed PAT auth attempts have tripped the
 * per-IP cooldown. An admin can clear a single IP (for example, a
 * legitimate user who pasted the wrong secret from the wrong vault).
 * Clearing is MFA-gated and recorded to the audit chain so an after
 * action review can attribute the override.
 *
 * Tenant scoped: an admin only sees locks associated with their
 * workspace, plus unattributed (anonymous probe) locks which any
 * admin can investigate.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ShieldWarning,
  LockOpen,
  Warning,
  ArrowsClockwise,
  ShieldCheck,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface LockEntry {
  ip: string;
  failures: number;
  locked: boolean;
  locked_until: number;
  retry_after: number;
  last_tenant_id: string;
}

interface Settings {
  threshold: number;
  window_seconds: number;
  cooldown_seconds: number;
}

interface Overview {
  settings: Settings;
  locks: LockEntry[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: Overview }
  | { kind: "error"; status: number; message: string };

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = { ...extra };
  if (k) h["X-API-Key"] = k;
  return h;
}

function fmtRetry(seconds: number): string {
  if (seconds <= 0) return "any moment";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

export default function PatAuthLockoutPage() {
  const [, ] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [mfaCode, setMfaCode] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionOk, setActionOk] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/admin/pat-auth-lockout", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as Overview;
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
  }, [refresh]);

  const unlock = async (ip: string) => {
    setBusy(ip);
    setActionError(null);
    setActionOk(null);
    try {
      const r = await fetch(
        `/api/admin/pat-auth-lockout/${encodeURIComponent(ip)}`,
        {
          method: "DELETE",
          headers: authHeaders({
            "Content-Type": "application/json",
            ...(mfaCode ? { "X-MFA-Code": mfaCode } : {}),
          }),
          body: JSON.stringify({ reason: reason || "admin manual unlock" }),
        }
      );
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        setActionError(`${r.status} ${r.statusText} ${body}`.trim());
      } else {
        setActionOk(`Cleared lock for ${ip}`);
        const data = (await r.json()) as Overview;
        setState({ kind: "ready", data });
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <main className="min-h-screen bg-[var(--color-bg)] text-[var(--color-fg)] px-4 py-6 sm:px-6 sm:py-10">
      <div className="mx-auto w-full max-w-3xl space-y-6">
        <div className="space-y-3">
          <Link
            href="/admin"
            className="inline-flex items-center gap-1 text-xs font-mono uppercase tracking-widest text-[var(--color-dim)] hover:text-[var(--color-fg)]"
          >
            <ArrowLeft size={12} weight="duotone" />
            admin console
          </Link>
          <div className="flex items-center gap-2">
            <ShieldWarning size={20} weight="duotone" />
            <h1 className="text-lg font-semibold">PAT auth lockouts</h1>
          </div>
          <p className="text-sm text-[var(--color-dim)]">
            Source IPs whose recent personal access token auth attempts
            tripped the per IP cooldown. Locks scoped to your
            workspace are shown alongside anonymous probes that have
            no workspace attribution yet. Clearing a lock here is
            recorded to the audit chain.
          </p>
        </div>

        <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-4 space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[180px] space-y-1">
              <label
                htmlFor="mfa-code"
                className="block text-xs font-mono uppercase tracking-widest text-[var(--color-dim)]"
              >
                your MFA code
              </label>
              <input
                id="mfa-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value.trim())}
                placeholder="six digit code"
                className="w-full rounded-md border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-sm font-mono tracking-widest focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
              />
            </div>
            <div className="flex-1 min-w-[180px] space-y-1">
              <label
                htmlFor="reason"
                className="block text-xs font-mono uppercase tracking-widest text-[var(--color-dim)]"
              >
                unlock reason
              </label>
              <input
                id="reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="ticket id or note"
                maxLength={500}
                className="w-full rounded-md border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
              />
            </div>
            <button
              type="button"
              onClick={refresh}
              className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-line)] px-2.5 py-1.5 text-xs font-mono uppercase tracking-widest hover:bg-[var(--color-bg)]"
            >
              <ArrowsClockwise size={12} weight="duotone" />
              refresh
            </button>
          </div>
          <p className="text-[11px] text-[var(--color-dim)]">
            MFA code is required when MFA is enforced on admin
            endpoints. The reason is recorded next to the unlock in
            the audit log.
          </p>
        </section>

        {actionError ? (
          <div
            role="alert"
            className="rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200 flex items-start gap-2"
          >
            <Warning size={14} weight="duotone" />
            <span className="break-words">{actionError}</span>
          </div>
        ) : null}
        {actionOk ? (
          <div
            role="status"
            className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-200 flex items-start gap-2"
          >
            <ShieldCheck size={14} weight="duotone" />
            <span>{actionOk}</span>
          </div>
        ) : null}

        <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)]">
          {state.kind === "loading" ? (
            <div className="p-4 space-y-2" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-10 rounded-md bg-[var(--color-bg)] animate-pulse"
                />
              ))}
            </div>
          ) : state.kind === "error" ? (
            <div className="p-4 text-xs text-rose-300 flex items-start gap-2">
              <Warning size={14} weight="duotone" />
              <span>
                {state.status ? `${state.status} ` : ""}
                {state.message || "request failed"}
              </span>
            </div>
          ) : state.data.locks.length === 0 ? (
            <div className="p-6 text-center text-xs text-[var(--color-dim)] space-y-1">
              <ShieldCheck
                size={22}
                weight="duotone"
                className="mx-auto opacity-70"
              />
              <p>No IPs are currently locked.</p>
              <p>
                Policy: lock after {state.data.settings.threshold} failed
                PAT auth attempts within{" "}
                {state.data.settings.window_seconds}s; cooldown lasts{" "}
                {state.data.settings.cooldown_seconds}s.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-[var(--color-line)]">
              {state.data.locks.map((item) => (
                <li
                  key={item.ip}
                  className="p-3 sm:p-4 flex flex-col sm:flex-row sm:items-center gap-3"
                >
                  <div className="flex-1 min-w-0 space-y-1">
                    <p
                      className="text-xs font-mono truncate"
                      title={item.ip}
                    >
                      {item.ip}
                    </p>
                    <p className="text-[11px] text-[var(--color-dim)]">
                      retry available in {fmtRetry(item.retry_after)}
                      {item.last_tenant_id
                        ? ` · attributed to ${item.last_tenant_id}`
                        : " · anonymous probe"}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy === item.ip}
                    onClick={() => unlock(item.ip)}
                    className="inline-flex items-center justify-center gap-1.5 rounded-md border border-[var(--color-line)] px-3 py-1.5 text-xs font-mono uppercase tracking-widest hover:bg-[var(--color-bg)] disabled:opacity-50"
                  >
                    <LockOpen size={12} weight="duotone" />
                    {busy === item.ip ? "clearing..." : "clear lock"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
