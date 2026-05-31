"use client";

/**
 * Workspace seat license administration.
 *
 * Owners set the maximum number of active plus pending members the
 * workspace can hold. The backend enforces the cap on every invite
 * and every SSO auto-join via member_store.check_capacity, returning
 * HTTP 402 Payment Required with a structured upgrade hint when the
 * caller would overflow. Setting the cap to 0 means unlimited (the
 * default for workspaces with no contract attached). Mutations
 * require admin role plus a fresh MFA code, matching every other
 * destructive workspace knob.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  UsersThree,
  ArrowLeft,
  Warning,
  CheckCircle,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface SeatLimitOut {
  tenant_id: string;
  limit: number;
  used: number;
  remaining: number;
  updated_by: string;
  updated_at: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: SeatLimitOut }
  | { kind: "error"; status: number; message: string };

function authHeaders(mfa?: string): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  if (mfa) h["X-MFA-Code"] = mfa;
  return h;
}

function formatTs(ts: number): string {
  if (!ts) return "Never";
  return new Date(ts * 1000).toLocaleString();
}

function formatLimit(n: number): string {
  return n === 0 ? "Unlimited" : n.toLocaleString();
}

export default function SeatLimitPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [limit, setLimit] = useState<number>(0);
  const [mfa, setMfa] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/workspace/seat-limit", {
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
      const data = (await r.json()) as SeatLimitOut;
      setState({ kind: "ready", data });
      setLimit(data.limit);
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

  async function save() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const r = await fetch("/api/workspace/seat-limit", {
        method: "PUT",
        headers: { ...authHeaders(mfa), "Content-Type": "application/json" },
        body: JSON.stringify({ limit }),
      });
      if (!r.ok) {
        const body = await r.text();
        if (
          r.status === 401 &&
          r.headers.get("www-authenticate")?.toLowerCase().includes("mfa")
        ) {
          setSaveError("MFA code required. Enter your TOTP code below.");
        } else {
          setSaveError(body || r.statusText);
        }
        return;
      }
      setSaved(true);
      setMfa("");
      await refresh();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const ready = state.kind === "ready" ? state.data : null;
  const willReduceBelowUsage =
    ready !== null && limit > 0 && limit < ready.used;
  const utilization =
    ready !== null && ready.limit > 0
      ? Math.min(100, Math.round((ready.used / ready.limit) * 100))
      : 0;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        <ArrowLeft size={14} weight="duotone" />
        Settings
      </Link>
      <div className="mt-3 flex items-start gap-3">
        <div className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">
          <UsersThree size={20} weight="duotone" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Seat license
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Cap the number of active plus pending members in this
            workspace. Invites and SSO auto-join are refused with HTTP
            402 once the cap is reached. Set to 0 for unlimited.
          </p>
        </div>
      </div>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-3">
          <div className="h-24 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
          <div className="h-40 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          <Warning size={18} weight="duotone" className="mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">
              Could not load seat license ({state.status})
            </div>
            <div className="mt-1 break-all">{state.message}</div>
            {state.status === 401 && (
              <div className="mt-2">
                Set your API key in{" "}
                <Link href="/settings" className="underline">
                  Settings
                </Link>
                . Admin role required.
              </div>
            )}
            {state.status === 403 && (
              <div className="mt-2">
                Reader role can view the cap. Admin role plus MFA is
                required to change it.
              </div>
            )}
          </div>
        </div>
      )}

      {ready && (
        <>
          <section className="mt-8 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <div className="text-xs uppercase tracking-wider text-zinc-500">
                  Seats used
                </div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">
                  {ready.used.toLocaleString()}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-zinc-500">
                  Cap
                </div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">
                  {formatLimit(ready.limit)}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-zinc-500">
                  Remaining
                </div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">
                  {ready.remaining === -1
                    ? "Unlimited"
                    : ready.remaining.toLocaleString()}
                </div>
              </div>
            </div>

            {ready.limit > 0 && (
              <div className="mt-4">
                <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-900">
                  <div
                    className={
                      "h-full " +
                      (utilization >= 100
                        ? "bg-red-500"
                        : utilization >= 80
                          ? "bg-amber-500"
                          : "bg-emerald-500")
                    }
                    style={{ width: `${utilization}%` }}
                    role="progressbar"
                    aria-valuenow={utilization}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  />
                </div>
                <div className="mt-1 text-xs text-zinc-500 tabular-nums">
                  {utilization}% utilized
                </div>
              </div>
            )}

            <div className="mt-4 flex items-center gap-2 text-xs text-zinc-500">
              <span>Last changed by</span>
              <code className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono dark:bg-zinc-900">
                {ready.updated_by || "never"}
              </code>
              <span>at</span>
              <span className="tabular-nums">{formatTs(ready.updated_at)}</span>
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="flex items-center gap-2">
              <LockKey size={16} weight="duotone" />
              <h2 className="text-sm font-medium">Change the cap</h2>
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              Admin role plus a fresh TOTP code is required. The change
              is recorded in the audit log.
            </p>

            <label
              htmlFor="seat-limit-input"
              className="mt-4 block text-xs uppercase tracking-wider text-zinc-500"
            >
              Seat cap (0 means unlimited)
            </label>
            <input
              id="seat-limit-input"
              type="number"
              inputMode="numeric"
              min={0}
              max={1000000}
              value={limit}
              onChange={(e) => {
                const n = Number.parseInt(e.target.value || "0", 10);
                setLimit(Number.isFinite(n) && n >= 0 ? n : 0);
              }}
              className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm tabular-nums focus:border-zinc-400 focus:outline-none dark:border-zinc-800 dark:bg-zinc-950"
            />

            {willReduceBelowUsage && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
                <div>
                  This workspace already uses {ready.used.toLocaleString()}{" "}
                  seats. Existing members keep working, but you will not
                  be able to invite anyone new until the cap is raised
                  or seats are revoked.
                </div>
              </div>
            )}

            <label
              htmlFor="mfa-input"
              className="mt-4 block text-xs uppercase tracking-wider text-zinc-500"
            >
              MFA code (if enrolled)
            </label>
            <input
              id="mfa-input"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              value={mfa}
              onChange={(e) => setMfa(e.target.value.replace(/\s+/g, ""))}
              className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm tabular-nums focus:border-zinc-400 focus:outline-none dark:border-zinc-800 dark:bg-zinc-950"
            />

            <div className="mt-4 flex items-center gap-3">
              <button
                type="button"
                onClick={save}
                disabled={saving || limit === ready.limit}
                className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                {saving ? "Saving..." : "Save cap"}
              </button>
              {saved && (
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                  <CheckCircle size={14} weight="duotone" />
                  Saved
                </span>
              )}
            </div>

            {saveError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
                <div className="break-all">{saveError}</div>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
