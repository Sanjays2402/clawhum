"use client";

/**
 * Admin view of MFA brute-force lockouts.
 *
 * Lists actors in this workspace whose MFA code submissions have
 * tripped the per-actor cooldown. An admin can clear an individual
 * lock (for example, a user lost their phone and called in) which is
 * recorded both in the lockout log and the tamper-evident audit
 * chain.
 *
 * The page is read-only when the list is empty so an empty state
 * cannot be misread as a missing fetch.
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

interface Lockout {
  actor_id: string;
  failures: number;
  locked: boolean;
  locked_until: number;
  retry_after: number;
}

interface ListResp {
  threshold: number;
  window_seconds: number;
  cooldown_seconds: number;
  items: Lockout[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ListResp }
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

export default function MfaLockoutsPage() {
  const [, ] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [mfaCode, setMfaCode] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionOk, setActionOk] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const headers = authHeaders(mfaCode ? { "X-MFA-Code": mfaCode } : {});
      const r = await fetch("/api/admin/mfa/lockouts", {
        headers,
        cache: "no-store",
      });
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        setState({
          kind: "error",
          status: r.status,
          message: body || r.statusText,
        });
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
  }, [mfaCode]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const unlock = async (actor_id: string) => {
    setBusy(actor_id);
    setActionError(null);
    setActionOk(null);
    try {
      const r = await fetch("/api/admin/mfa/lockouts/unlock", {
        method: "POST",
        headers: authHeaders({
          "Content-Type": "application/json",
          ...(mfaCode ? { "X-MFA-Code": mfaCode } : {}),
        }),
        body: JSON.stringify({ actor_id, reason: "admin manual unlock" }),
      });
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        setActionError(`${r.status} ${r.statusText} ${body}`.trim());
      } else {
        setActionOk(`Cleared lock for ${actor_id}`);
        await refresh();
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
            <h1 className="text-lg font-semibold">MFA lockouts</h1>
          </div>
          <p className="text-sm text-[var(--color-dim)]">
            Actors whose recent MFA code submissions have tripped the
            per actor cooldown. Clearing a lock here is logged to the
            audit chain so an after action review can attribute the
            override to a specific admin actor.
          </p>
        </div>

        <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-4 space-y-3">
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
          <p className="text-[11px] text-[var(--color-dim)]">
            Required when MFA is enforced on admin endpoints. The same
            code authorises the read and any subsequent unlock click.
          </p>
          <button
            type="button"
            onClick={refresh}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-line)] px-2.5 py-1.5 text-xs font-mono uppercase tracking-widest hover:bg-[var(--color-bg)]"
          >
            <ArrowsClockwise size={12} weight="duotone" />
            refresh
          </button>
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
          ) : state.data.items.length === 0 ? (
            <div className="p-6 text-center text-xs text-[var(--color-dim)] space-y-1">
              <ShieldCheck
                size={22}
                weight="duotone"
                className="mx-auto opacity-70"
              />
              <p>No actors are currently locked.</p>
              <p>
                Policy: lock after {state.data.threshold} failed codes
                within {state.data.window_seconds}s; cooldown lasts{" "}
                {state.data.cooldown_seconds}s.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-[var(--color-line)]">
              {state.data.items.map((item) => (
                <li
                  key={item.actor_id}
                  className="p-3 sm:p-4 flex flex-col sm:flex-row sm:items-center gap-3"
                >
                  <div className="flex-1 min-w-0 space-y-1">
                    <p className="text-xs font-mono truncate" title={item.actor_id}>
                      {item.actor_id}
                    </p>
                    <p className="text-[11px] text-[var(--color-dim)]">
                      retry available in {fmtRetry(item.retry_after)}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy === item.actor_id}
                    onClick={() => unlock(item.actor_id)}
                    className="inline-flex items-center justify-center gap-1.5 rounded-md border border-[var(--color-line)] px-3 py-1.5 text-xs font-mono uppercase tracking-widest hover:bg-[var(--color-bg)] disabled:opacity-50"
                  >
                    <LockOpen size={12} weight="duotone" />
                    {busy === item.actor_id ? "clearing..." : "clear lock"}
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
