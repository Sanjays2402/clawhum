"use client";

/**
 * Workspace quota plan administration.
 *
 * Admins pick a plan or set custom ceilings. The backend enforces the
 * numbers on every request via the rate-limit middleware, so changes
 * here take effect on the next request. MFA is required to save.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Gauge,
  ArrowLeft,
  Warning,
  CheckCircle,
  LockKey,
  Lightning,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PlanOut {
  tenant_id: string;
  plan: string;
  rpm_ceiling: number;
  daily_quota: number;
  updated_at: number;
  updated_by: string;
}

interface CatalogEntry {
  name: string;
  rpm_ceiling: number;
  daily_quota: number;
}

interface ReadResp {
  plan: PlanOut;
  catalog: CatalogEntry[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ReadResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(mfa?: string): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  if (mfa) h["X-MFA-Code"] = mfa;
  return h;
}

function formatNumber(n: number): string {
  if (n === 0) return "Unlimited";
  return n.toLocaleString();
}

function formatTs(ts: number): string {
  if (!ts) return "Never";
  return new Date(ts * 1000).toLocaleString();
}

export default function QuotasPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [planName, setPlanName] = useState("enterprise");
  const [rpm, setRpm] = useState(0);
  const [day, setDay] = useState(0);
  const [mfa, setMfa] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/quotas", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as ReadResp;
      setState({ kind: "ready", data });
      setPlanName(data.plan.plan);
      setRpm(data.plan.rpm_ceiling);
      setDay(data.plan.daily_quota);
    } catch (err) {
      setState({ kind: "error", status: 0, message: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  const catalog = useMemo(() => (state.kind === "ready" ? state.data.catalog : []), [state]);

  function pickPreset(name: string) {
    setPlanName(name);
    const preset = catalog.find((p) => p.name === name);
    if (preset && name !== "custom") {
      setRpm(preset.rpm_ceiling);
      setDay(preset.daily_quota);
    }
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const r = await fetch("/api/quotas", {
        method: "PUT",
        headers: { ...authHeaders(mfa), "Content-Type": "application/json" },
        body: JSON.stringify({ plan: planName, rpm_ceiling: rpm, daily_quota: day }),
      });
      if (!r.ok) {
        const body = await r.text();
        if (r.status === 401 && r.headers.get("www-authenticate")?.toLowerCase().includes("mfa")) {
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
          <Gauge size={20} weight="duotone" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Workspace quota</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Aggregate request ceilings for this workspace. Applied across every API key
            and IP. Per-key limits still apply on top.
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
            <div className="font-medium">Could not load plan ({state.status})</div>
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
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <section className="mt-8 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
                Current plan
              </h2>
              <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium dark:bg-zinc-900">
                {state.data.plan.plan}
              </span>
            </div>
            <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-zinc-500">Per-minute ceiling</dt>
                <dd className="mt-1 text-xl font-semibold">
                  {formatNumber(state.data.plan.rpm_ceiling)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-500">Daily quota</dt>
                <dd className="mt-1 text-xl font-semibold">
                  {formatNumber(state.data.plan.daily_quota)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-500">Last updated</dt>
                <dd className="mt-1 text-sm">
                  {formatTs(state.data.plan.updated_at)}
                  {state.data.plan.updated_by ? (
                    <div className="text-xs text-zinc-500">by {state.data.plan.updated_by}</div>
                  ) : null}
                </dd>
              </div>
            </dl>
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Change plan
            </h2>
            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-5">
              {catalog.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  onClick={() => pickPreset(p.name)}
                  className={`rounded-md border px-3 py-2 text-left text-sm transition ${
                    planName === p.name
                      ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                      : "border-zinc-200 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600"
                  }`}
                >
                  <div className="font-medium capitalize">{p.name}</div>
                  <div className="mt-1 text-xs opacity-75">
                    {formatNumber(p.rpm_ceiling)} rpm
                  </div>
                  <div className="text-xs opacity-75">{formatNumber(p.daily_quota)} / day</div>
                </button>
              ))}
            </div>

            <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="text-zinc-700 dark:text-zinc-300">RPM ceiling</span>
                <input
                  type="number"
                  min={0}
                  value={rpm}
                  onChange={(e) => setRpm(Math.max(0, Number(e.target.value) || 0))}
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm tabular-nums focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
                />
                <span className="mt-1 block text-xs text-zinc-500">0 means unlimited.</span>
              </label>
              <label className="block text-sm">
                <span className="text-zinc-700 dark:text-zinc-300">Daily quota</span>
                <input
                  type="number"
                  min={0}
                  value={day}
                  onChange={(e) => setDay(Math.max(0, Number(e.target.value) || 0))}
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm tabular-nums focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
                />
                <span className="mt-1 block text-xs text-zinc-500">0 means unlimited.</span>
              </label>
            </div>

            <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              <LockKey size={14} weight="duotone" className="mt-0.5 shrink-0" />
              <span>
                Saving requires MFA. If your account is enrolled, enter the current 6 digit TOTP
                code.
              </span>
            </div>

            <label className="mt-3 block text-sm">
              <span className="text-zinc-700 dark:text-zinc-300">MFA code</span>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={mfa}
                onChange={(e) => setMfa(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="123456"
                className="mt-1 w-32 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm tabular-nums focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
              />
            </label>

            <div className="mt-5 flex items-center gap-3">
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3.5 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                <Lightning size={14} weight="duotone" />
                {saving ? "Saving" : "Save plan"}
              </button>
              {saved && (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-600 dark:text-emerald-400">
                  <CheckCircle size={14} weight="duotone" />
                  Saved
                </span>
              )}
            </div>
            {saveError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
                <span className="break-all">{saveError}</span>
              </div>
            )}
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 bg-white p-5 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
            <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Response headers
            </h3>
            <p className="mt-2">
              The API returns standard headers so clients can adapt automatically.
            </p>
            <ul className="mt-2 space-y-1 font-mono text-xs">
              <li>X-RateLimit-Limit</li>
              <li>X-RateLimit-Remaining</li>
              <li>X-RateLimit-Reset (unix seconds)</li>
              <li>X-RateLimit-Scope (key, workspace_minute, workspace_day)</li>
              <li>X-RateLimit-Plan</li>
              <li>X-RateLimit-Limit-Day, X-RateLimit-Remaining-Day</li>
              <li>Retry-After (on 429)</li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
