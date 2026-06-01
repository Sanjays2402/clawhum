"use client";

/**
 * Workspace retention floor console.
 *
 * Per category minimum TTLs that every future PUT /retention call
 * must satisfy. SOC2 reviewers want evidence that a single careless
 * (or compromised) admin cannot shrink audit retention from 365
 * days to 7 right before deleting evidence of misuse. Setting a
 * floor here pins the lower bound. Value 0 (keep forever) is
 * always allowed because it strictly increases retention.
 *
 * Strictly per workspace; the backend enforces tenant scoping.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ShieldCheck,
  Warning,
  CheckCircle,
  Lock,
} from "@phosphor-icons/react/dist/ssr";
import { API_BASE } from "@/lib/api";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface FloorView {
  tenant_id: string;
  history_days: number;
  feedback_days: number;
  audit_days: number;
  webhook_deliveries_days: number;
  updated_at: number;
  updated_by: string;
  ceiling: number;
}

const FIELDS: { key: keyof FloorView & string; label: string; note: string }[] = [
  { key: "history_days", label: "history", note: "match history rows" },
  { key: "feedback_days", label: "feedback", note: "user feedback rows" },
  { key: "audit_days", label: "audit", note: "audit log entries" },
  {
    key: "webhook_deliveries_days",
    label: "webhook deliveries",
    note: "outbound delivery records",
  },
];

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

export default function RetentionFloorPage() {
  useApiKey();
  const [data, setData] = useState<FloorView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({
    history_days: "0",
    feedback_days: "0",
    audit_days: "0",
    webhook_deliveries_days: "0",
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(API_BASE + "/retention-floor", {
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as FloorView;
      setData(j);
      setDraft({
        history_days: String(j.history_days),
        feedback_days: String(j.feedback_days),
        audit_days: String(j.audit_days),
        webhook_deliveries_days: String(j.webhook_deliveries_days),
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = useCallback(async () => {
    setBusy(true);
    setMsg(null);
    setActionErr(null);
    try {
      const payload: Record<string, number> = {};
      for (const f of FIELDS) {
        const n = Number(draft[f.key]);
        if (!Number.isFinite(n) || !Number.isInteger(n) || n < 0) {
          throw new Error(`${f.label}: enter a non negative integer`);
        }
        if (data && n > data.ceiling) {
          throw new Error(`${f.label}: cannot exceed ${data.ceiling}`);
        }
        payload[f.key] = n;
      }
      const r = await fetch(API_BASE + "/retention-floor", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) {
        const detail = j?.detail?.message || j?.detail || `HTTP ${r.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setData(j as FloorView);
      setMsg("Retention floor updated. Future PUT /retention calls cannot drop below these values.");
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }, [draft, data]);

  const anyFloor =
    !!data &&
    (data.history_days > 0 ||
      data.feedback_days > 0 ||
      data.audit_days > 0 ||
      data.webhook_deliveries_days > 0);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
      <div className="mb-6 flex items-center gap-3 text-sm text-[var(--color-dim)]">
        <Link
          href="/admin"
          className="inline-flex items-center gap-1 rounded-md border border-transparent px-2 py-1 hover:border-[var(--color-border)] hover:text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
        >
          <ArrowLeft size={14} weight="duotone" />
          Admin
        </Link>
      </div>

      <header className="mb-8">
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-[var(--color-border)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--color-dim)]">
          <Lock size={14} weight="duotone" />
          Retention floor
        </div>
        <h1 className="text-2xl font-semibold sm:text-3xl">
          Minimum retention days
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-dim)]">
          Pin a lower bound on how long each category of data must be
          kept. Once set, the retention form refuses to drop any
          positive value below the floor. Value 0 (keep forever) is
          always allowed because it strictly increases retention.
          Reduce or lift the floor here. Every change is audit logged
          and requires MFA step up.
        </p>
      </header>

      {loading ? (
        <div className="space-y-3" aria-busy="true">
          <div className="h-24 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
          <div className="h-48 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/5 p-5 text-sm text-rose-300">
          <div className="mb-1 flex items-center gap-2 font-medium">
            <Warning size={16} weight="duotone" /> Could not load floor
          </div>
          <p className="text-rose-200/80">{error}</p>
          <button
            type="button"
            onClick={load}
            className="mt-3 rounded-md border border-rose-500/40 px-3 py-1 text-xs hover:bg-rose-500/10 focus:outline-none focus:ring-2 focus:ring-rose-400"
          >
            Retry
          </button>
        </div>
      ) : !data ? (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center text-sm text-[var(--color-dim)]">
          No floor data. Try reloading.
        </div>
      ) : (
        <div className="space-y-6">
          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--color-dim)]">
                  <Lock size={14} weight="duotone" />
                  Current state
                </div>
                <div className="mt-1 text-lg font-semibold">
                  {anyFloor ? "Floor active" : "No floor set"}
                </div>
                <div className="mt-1 text-xs text-[var(--color-dim)]">
                  {anyFloor
                    ? "Retention reductions below these values are blocked"
                    : "Retention can be set to any in range value"}
                </div>
              </div>
              <div className="text-right text-xs text-[var(--color-dim)]">
                <div>Updated by</div>
                <div className="font-mono text-[var(--color-text)]">
                  {data.updated_by || "unset"}
                </div>
                <div className="mt-1">{fmtDate(data.updated_at)}</div>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h2 className="text-sm font-semibold">Per category floor (days)</h2>
            <p className="mt-1 text-xs text-[var(--color-dim)]">
              0 means no floor for that category. Maximum {data.ceiling}.
              MFA step up required to save.
            </p>
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {FIELDS.map((f) => (
                <label key={f.key} className="block">
                  <div className="mb-1 flex items-baseline justify-between text-xs">
                    <span className="font-medium text-[var(--color-text)]">{f.label}</span>
                    <span className="text-[var(--color-dim)]">{f.note}</span>
                  </div>
                  <input
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={data.ceiling}
                    step={1}
                    value={draft[f.key]}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, [f.key]: e.target.value }))
                    }
                    disabled={busy}
                    aria-label={`${f.label} floor in days`}
                    className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 font-mono text-sm tabular-nums focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40"
                  />
                </label>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={save}
                disabled={busy}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-2 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-accent)]/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ShieldCheck size={14} weight="duotone" />
                {busy ? "Saving" : "Save floor"}
              </button>
            </div>
          </section>

          {msg && (
            <div className="flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/5 px-3 py-2 text-sm text-emerald-300">
              <CheckCircle size={14} weight="duotone" />
              {msg}
            </div>
          )}
          {actionErr && (
            <div className="flex items-center gap-2 rounded-md border border-rose-500/40 bg-rose-500/5 px-3 py-2 text-sm text-rose-300">
              <Warning size={14} weight="duotone" />
              {actionErr}
            </div>
          )}
        </div>
      )}
    </main>
  );
}
