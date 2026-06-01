"use client";

/**
 * Workspace PAT minimum-requirements console.
 *
 * SOC2 CC6.1 and ISO 27001 A.9.2.1 ask whether credentials carry an
 * identifiable owner, a bounded lifetime, and network scope. This
 * screen lets a workspace admin pin those minimums for every PAT
 * minted in this workspace from this point forward. Existing tokens
 * are untouched so a rollout does not pull the rug out of production.
 *
 * MFA gated. Strictly per-workspace; the backend enforces tenant
 * scoping and writes every change to the audit log.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ShieldCheck,
  Warning,
  CheckCircle,
  Key,
  EnvelopeSimple,
  Clock,
  Network,
} from "@phosphor-icons/react/dist/ssr";
import { API_BASE } from "@/lib/api";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyView {
  enforcing: boolean;
  require_owner_email: boolean;
  require_expiry: boolean;
  max_expiry_days: number;
  require_ip_cidrs: boolean;
  max_expiry_days_ceiling: number;
  updated_at: number;
  updated_by: string;
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

export default function PatMinRequirementsPage() {
  useApiKey();
  const [data, setData] = useState<PolicyView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const [reqOwner, setReqOwner] = useState(false);
  const [reqExpiry, setReqExpiry] = useState(false);
  const [maxDays, setMaxDays] = useState<string>("0");
  const [reqCidrs, setReqCidrs] = useState(false);

  const sync = useCallback((j: PolicyView) => {
    setData(j);
    setReqOwner(j.require_owner_email);
    setReqExpiry(j.require_expiry);
    setMaxDays(String(j.max_expiry_days || 0));
    setReqCidrs(j.require_ip_cidrs);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(API_BASE + "/pat-min-requirements", {
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      sync((await r.json()) as PolicyView);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [sync]);

  useEffect(() => {
    load();
  }, [load]);

  const save = useCallback(async () => {
    setBusy(true);
    setMsg(null);
    setActionErr(null);
    const n = Number(maxDays);
    if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
      setActionErr("Max expiry days must be a non negative integer");
      setBusy(false);
      return;
    }
    if (data && n > data.max_expiry_days_ceiling) {
      setActionErr(`Cannot exceed ${data.max_expiry_days_ceiling}`);
      setBusy(false);
      return;
    }
    try {
      const r = await fetch(API_BASE + "/pat-min-requirements", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({
          require_owner_email: reqOwner,
          require_expiry: reqExpiry,
          max_expiry_days: n,
          require_ip_cidrs: reqCidrs,
        }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) {
        const detail = j?.detail?.message || j?.detail || `HTTP ${r.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      sync(j as PolicyView);
      setMsg("Policy saved. New mints are checked against these floors.");
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }, [reqOwner, reqExpiry, reqCidrs, maxDays, data, sync]);

  const clearAll = useCallback(async () => {
    setReqOwner(false);
    setReqExpiry(false);
    setMaxDays("0");
    setReqCidrs(false);
    setBusy(true);
    setMsg(null);
    setActionErr(null);
    try {
      const r = await fetch(API_BASE + "/pat-min-requirements", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({
          require_owner_email: false,
          require_expiry: false,
          max_expiry_days: 0,
          require_ip_cidrs: false,
        }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) {
        const detail = j?.detail?.message || j?.detail || `HTTP ${r.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      sync(j as PolicyView);
      setMsg("Policy cleared. PAT mints no longer require these attributes.");
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }, [sync]);

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
          <Key size={14} weight="duotone" />
          API key minimums
        </div>
        <h1 className="text-2xl font-semibold sm:text-3xl">
          Required attributes for new API keys
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-dim)]">
          Pin the floor security attributes any new personal access token
          must carry in this workspace. Existing tokens are untouched. The
          backend rejects non compliant mints with a structured 400 and
          writes every policy change to the audit log.
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
            <Warning size={16} weight="duotone" /> Could not load policy
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
          No policy data. Try reloading.
        </div>
      ) : (
        <div className="space-y-6">
          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--color-dim)]">
                  <ShieldCheck size={14} weight="duotone" />
                  Current state
                </div>
                <div className="mt-1 text-2xl font-semibold">
                  {data.enforcing ? "Enforcing" : "Off"}
                </div>
                <div className="mt-1 text-xs text-[var(--color-dim)]">
                  {data.enforcing
                    ? "New PAT mints are validated against the floor."
                    : "New PAT mints are not validated. Existing tokens unchanged."}
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
            <h2 className="text-sm font-semibold">Required fields</h2>
            <p className="mt-1 text-xs text-[var(--color-dim)]">
              MFA step up required to save. Toggle the controls then press save.
            </p>

            <div className="mt-4 space-y-3">
              <label className="flex cursor-pointer items-start gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 hover:border-[var(--color-text)]/40">
                <input
                  type="checkbox"
                  checked={reqOwner}
                  onChange={(e) => setReqOwner(e.target.checked)}
                  disabled={busy}
                  className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
                  aria-label="Require owner email"
                />
                <div className="flex-1">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <EnvelopeSimple size={14} weight="duotone" />
                    Require owner email
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-dim)]">
                    Reject PAT mints without a non blank owner_email so every
                    token can be traced to a person.
                  </div>
                </div>
              </label>

              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={reqExpiry}
                    onChange={(e) => setReqExpiry(e.target.checked)}
                    disabled={busy}
                    className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
                    aria-label="Require expiry"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <Clock size={14} weight="duotone" />
                      Require expiry
                    </div>
                    <div className="mt-1 text-xs text-[var(--color-dim)]">
                      Reject mints with no expires_in_days. Set a cap to
                      block tokens that try to live too long.
                    </div>
                  </div>
                </label>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                  <label
                    htmlFor="max-days"
                    className="text-xs text-[var(--color-dim)] sm:w-44"
                  >
                    Max expiry days
                    <span className="ml-1 text-[var(--color-dim)]/60">
                      (0 = no cap)
                    </span>
                  </label>
                  <input
                    id="max-days"
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={data.max_expiry_days_ceiling}
                    step={1}
                    value={maxDays}
                    onChange={(e) => setMaxDays(e.target.value)}
                    disabled={busy || !reqExpiry}
                    className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 font-mono text-sm tabular-nums focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40 sm:max-w-xs disabled:opacity-50"
                  />
                </div>
              </div>

              <label className="flex cursor-pointer items-start gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 hover:border-[var(--color-text)]/40">
                <input
                  type="checkbox"
                  checked={reqCidrs}
                  onChange={(e) => setReqCidrs(e.target.checked)}
                  disabled={busy}
                  className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
                  aria-label="Require IP CIDR scope"
                />
                <div className="flex-1">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Network size={14} weight="duotone" />
                    Require IP CIDR scope
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-dim)]">
                    Reject mints with no ip_cidrs entries so every token is
                    network scoped from the moment it is issued.
                  </div>
                </div>
              </label>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={save}
                disabled={busy}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-2 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-accent)]/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ShieldCheck size={14} weight="duotone" />
                {busy ? "Saving" : "Save policy"}
              </button>
              <button
                type="button"
                onClick={clearAll}
                disabled={busy}
                className="inline-flex items-center justify-center rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-dim)] transition hover:border-[var(--color-text)]/40 hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Clear policy
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
