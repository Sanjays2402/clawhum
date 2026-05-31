"use client";

/**
 * Workspace monthly budget cap console.
 *
 * Procurement and finance teams require a hard ceiling on billable
 * consumption per workspace. This screen reads the current cap, the
 * rolling 30 day usage, the soft warn threshold, and lets admins
 * (MFA gated) raise the cap, change the warn point, or flip into
 * audit-only rollout mode.
 *
 * Strictly per-workspace; the backend enforces tenant scoping.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Coins,
  Warning,
  CheckCircle,
  Gauge,
  ShieldCheck,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface BudgetView {
  tenant_id: string;
  monthly_cap: number;
  soft_threshold_pct: number;
  hard_stop: boolean;
  notes: string;
  updated_at: number;
  updated_by: string;
}

interface BudgetStatus {
  budget: BudgetView;
  used: number;
  remaining: number;
  percent_used: number;
  status: "ok" | "warning" | "exhausted" | "unset";
  window_sec: number;
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (k) h["X-API-Key"] = k;
  return h;
}

function fmtDate(ts: number): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";
  } catch {
    return String(ts);
  }
}

function fmtInt(n: number): string {
  return new Intl.NumberFormat("en-US").format(Math.max(0, n));
}

const STATUS_STYLES: Record<BudgetStatus["status"], string> = {
  ok: "text-emerald-400 border-emerald-500/40 bg-emerald-500/5",
  warning: "text-amber-300 border-amber-500/40 bg-amber-500/5",
  exhausted: "text-rose-300 border-rose-500/40 bg-rose-500/5",
  unset: "text-[var(--color-dim)] border-[var(--color-border)]",
};

export default function BudgetPage() {
  useApiKey();
  const [data, setData] = useState<BudgetStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const [cap, setCap] = useState<string>("0");
  const [warn, setWarn] = useState<string>("80");
  const [hard, setHard] = useState<boolean>(true);
  const [notes, setNotes] = useState<string>("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/budget", { headers: authHeaders(), cache: "no-store" });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      const j = (await r.json()) as BudgetStatus;
      setData(j);
      setCap(String(j.budget.monthly_cap));
      setWarn(String(j.budget.soft_threshold_pct));
      setHard(j.budget.hard_stop);
      setNotes(j.budget.notes || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = useCallback(async () => {
    setBusy(true);
    setActionErr(null);
    setMsg(null);
    try {
      const r = await fetch("/api/budget", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({
          monthly_cap: Math.max(0, Number(cap) | 0),
          soft_threshold_pct: Math.max(0, Math.min(100, Number(warn) | 0)),
          hard_stop: hard,
          notes: notes.slice(0, 280),
        }),
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      const j = (await r.json()) as BudgetStatus;
      setData(j);
      setMsg("Saved. New budget is enforced on the next request.");
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [cap, warn, hard, notes]);

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-fg)]">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
        <Link
          href="/admin"
          className="inline-flex items-center gap-1 text-xs text-[var(--color-dim)] hover:text-[var(--color-fg)]"
        >
          <ArrowLeft size={12} weight="duotone" />
          back to admin
        </Link>

        <header className="mt-4 mb-8">
          <div className="flex items-center gap-2">
            <Coins size={18} weight="duotone" />
            <h1 className="text-lg font-medium">monthly budget cap</h1>
          </div>
          <p className="mt-2 text-sm text-[var(--color-dim)]">
            Hard ceiling on chargeable requests for this workspace over
            a rolling 30 day window. Bounds the month while rate limits
            bound the rate. Required by most finance and procurement
            reviews. Admin only and step-up MFA gated. Changes are
            written to the audit log.
          </p>
        </header>

        {loading ? (
          <div className="rounded-md border border-[var(--color-border)] p-6">
            <div className="h-3 w-32 animate-pulse rounded bg-[var(--color-border)]" />
            <div className="mt-3 h-3 w-48 animate-pulse rounded bg-[var(--color-border)]" />
          </div>
        ) : error ? (
          <div className="rounded-md border border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-300">
            <div className="flex items-center gap-2">
              <Warning size={14} weight="duotone" />
              <span>Could not load budget.</span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap text-xs opacity-80">{error}</pre>
          </div>
        ) : data ? (
          <div className="space-y-6">
            <section
              className={`rounded-md border p-5 ${STATUS_STYLES[data.status]}`}
            >
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide">
                <Gauge size={14} weight="duotone" />
                <span>current window</span>
                <span className="ml-auto font-mono text-[10px] opacity-70">
                  status: {data.status}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap items-end gap-x-8 gap-y-3">
                <div>
                  <div className="text-[10px] uppercase tracking-wide opacity-70">
                    used
                  </div>
                  <div className="font-mono text-2xl">{fmtInt(data.used)}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide opacity-70">
                    cap
                  </div>
                  <div className="font-mono text-2xl">
                    {data.budget.monthly_cap > 0
                      ? fmtInt(data.budget.monthly_cap)
                      : "unset"}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide opacity-70">
                    remaining
                  </div>
                  <div className="font-mono text-2xl">
                    {data.budget.monthly_cap > 0 ? fmtInt(data.remaining) : "infinite"}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide opacity-70">
                    used %
                  </div>
                  <div className="font-mono text-2xl">
                    {data.budget.monthly_cap > 0 ? data.percent_used.toFixed(1) : "0.0"}
                  </div>
                </div>
              </div>
              {data.budget.monthly_cap > 0 ? (
                <div
                  className="mt-4 h-1.5 w-full overflow-hidden rounded bg-black/30"
                  aria-hidden="true"
                >
                  <div
                    className="h-full bg-current"
                    style={{
                      width: `${Math.min(100, data.percent_used).toFixed(1)}%`,
                    }}
                  />
                </div>
              ) : null}
            </section>

            <section className="rounded-md border border-[var(--color-border)] p-5">
              <div className="text-xs uppercase tracking-wide text-[var(--color-dim)]">
                last updated
              </div>
              <dl className="mt-3 grid grid-cols-1 gap-2 text-sm sm:grid-cols-[140px_1fr]">
                <dt className="text-[var(--color-dim)]">workspace</dt>
                <dd className="font-mono">{data.budget.tenant_id}</dd>
                <dt className="text-[var(--color-dim)]">updated at</dt>
                <dd className="font-mono">{fmtDate(data.budget.updated_at)}</dd>
                <dt className="text-[var(--color-dim)]">updated by</dt>
                <dd className="font-mono break-all">{data.budget.updated_by}</dd>
                <dt className="text-[var(--color-dim)]">enforcement</dt>
                <dd className="font-mono">
                  {data.budget.hard_stop ? "hard stop (402)" : "audit only"}
                </dd>
              </dl>
            </section>

            <section className="rounded-md border border-[var(--color-border)] p-5">
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-[var(--color-dim)]">
                <ShieldCheck size={14} weight="duotone" />
                update budget
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <label className="block text-sm">
                  <span className="text-[var(--color-dim)]">
                    monthly cap (chargeable requests, 0 = unlimited)
                  </span>
                  <input
                    type="number"
                    min={0}
                    inputMode="numeric"
                    value={cap}
                    onChange={(e) => setCap(e.target.value)}
                    className="mt-1 w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 font-mono text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="text-[var(--color-dim)]">
                    warn threshold (% of cap, 0 = off)
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    inputMode="numeric"
                    value={warn}
                    onChange={(e) => setWarn(e.target.value)}
                    className="mt-1 w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 font-mono text-sm"
                  />
                </label>
              </div>

              <label className="mt-4 flex items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={hard}
                  onChange={(e) => setHard(e.target.checked)}
                  className="mt-1"
                />
                <span>
                  <span className="block">
                    hard stop at cap (return HTTP 402)
                  </span>
                  <span className="block text-xs text-[var(--color-dim)]">
                    Off means audit only: requests still succeed past the
                    cap and the X-Budget-Status header surfaces the
                    overage so dashboards can alert without breaking
                    integrations during rollout.
                  </span>
                </span>
              </label>

              <label className="mt-4 block text-sm">
                <span className="text-[var(--color-dim)]">
                  notes (contract reference, optional)
                </span>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value.slice(0, 280))}
                  rows={2}
                  maxLength={280}
                  className="mt-1 w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm"
                />
              </label>

              <div className="mt-5 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={save}
                  disabled={busy}
                  className="inline-flex items-center gap-2 rounded border border-[var(--color-border)] bg-[var(--color-border)]/30 px-3 py-2 text-sm hover:bg-[var(--color-border)]/50 disabled:opacity-50"
                >
                  <CheckCircle size={14} weight="duotone" />
                  {busy ? "saving" : "save budget"}
                </button>
                {msg ? (
                  <span className="text-xs text-emerald-400">{msg}</span>
                ) : null}
                {actionErr ? (
                  <span className="text-xs text-rose-300 break-all">{actionErr}</span>
                ) : null}
              </div>
            </section>

            <p className="text-xs text-[var(--color-dim)]">
              Try it:{" "}
              <code className="font-mono">
                curl -H &quot;X-API-Key: $KEY&quot; https://api.clawhum.dev/v1/budget
              </code>
            </p>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-[var(--color-border)] p-5 text-sm text-[var(--color-dim)]">
            No data.
          </div>
        )}
      </div>
    </div>
  );
}
