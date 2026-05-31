"use client";

/**
 * Workspace data retention policy administration.
 *
 * Per-tenant TTL for history, feedback, audit, and webhook delivery
 * rows. The backend filters reads above the TTL immediately, and an
 * enforce sweep hard deletes anything older. Dry run reports counts
 * without touching disk. Admin role plus a fresh MFA code are
 * required to mutate the policy or run a real enforce.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Warning,
  CheckCircle,
  HourglassMedium,
  Broom,
  FloppyDisk,
  Eye,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyOut {
  tenant_id: string;
  history_days: number;
  feedback_days: number;
  audit_days: number;
  webhook_deliveries_days: number;
  updated_at: number;
  updated_by: string;
}

interface EnforceResponse {
  tenant_id: string;
  removed: Record<string, number>;
  ran_at: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; policy: PolicyOut }
  | { kind: "error"; status: number; message: string };

type Category =
  | "history_days"
  | "feedback_days"
  | "audit_days"
  | "webhook_deliveries_days";

const CATEGORIES: {
  key: Category;
  removedKey: string;
  label: string;
  hint: string;
}[] = [
  {
    key: "history_days",
    removedKey: "history",
    label: "match history",
    hint: "search/match log rows older than the TTL stop appearing in /history and get hard deleted on enforce.",
  },
  {
    key: "feedback_days",
    removedKey: "feedback",
    label: "feedback",
    hint: "thumbs up/down rows posted to /feedback.",
  },
  {
    key: "audit_days",
    removedKey: "audit",
    label: "audit log",
    hint: "tamper-evident mutation log. immutable while a legal hold is active.",
  },
  {
    key: "webhook_deliveries_days",
    removedKey: "webhook_deliveries",
    label: "webhook deliveries",
    hint: "per-delivery records under /webhooks/:id/deliveries.",
  },
];

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

export default function RetentionPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draft, setDraft] = useState<Record<Category, string>>({
    history_days: "0",
    feedback_days: "0",
    audit_days: "0",
    webhook_deliveries_days: "0",
  });
  const [mfa, setMfa] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [preview, setPreview] = useState<EnforceResponse | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/retention", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const policy = (await r.json()) as PolicyOut;
      setState({ kind: "ready", policy });
      setDraft({
        history_days: String(policy.history_days),
        feedback_days: String(policy.feedback_days),
        audit_days: String(policy.audit_days),
        webhook_deliveries_days: String(policy.webhook_deliveries_days),
      });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  function parseDraft(): Record<Category, number> | null {
    const out: Record<string, number> = {};
    for (const c of CATEGORIES) {
      const raw = draft[c.key].trim();
      if (raw === "") {
        setActionError(`${c.label}: enter 0 or a number of days`);
        return null;
      }
      const n = Number(raw);
      if (!Number.isInteger(n) || n < 0 || n > 3650) {
        setActionError(`${c.label}: must be an integer between 0 and 3650`);
        return null;
      }
      out[c.key] = n;
    }
    return out as Record<Category, number>;
  }

  async function savePolicy() {
    setActionError(null);
    setFlash(null);
    setPreview(null);
    const body = parseDraft();
    if (!body) return;
    if (!mfa.trim()) {
      setActionError("MFA code required to update policy");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/retention", {
        method: "PUT",
        headers: { ...authHeaders(mfa), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text();
        setActionError(`${r.status}: ${txt || r.statusText}`);
        return;
      }
      setMfa("");
      setFlash("Policy saved. Reads filter immediately; run enforce to purge from disk.");
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function runEnforce(dryRun: boolean) {
    setActionError(null);
    setFlash(null);
    setPreview(null);
    if (!dryRun && !mfa.trim()) {
      setActionError("MFA code required to run a real enforce");
      return;
    }
    setBusy(true);
    try {
      const headers = dryRun ? authHeaders() : authHeaders(mfa);
      const r = await fetch(`/api/retention/enforce?dry_run=${dryRun ? "true" : "false"}`, {
        method: "POST",
        headers,
      });
      if (!r.ok) {
        const txt = await r.text();
        setActionError(`${r.status}: ${txt || r.statusText}`);
        return;
      }
      const data = (await r.json()) as EnforceResponse;
      if (dryRun) {
        setPreview(data);
      } else {
        const total = Object.values(data.removed).reduce((s, n) => s + n, 0);
        setFlash(`Enforce completed. ${total} row${total === 1 ? "" : "s"} removed.`);
        setMfa("");
      }
    } finally {
      setBusy(false);
    }
  }

  const policyEmpty =
    state.kind === "ready" &&
    state.policy.history_days === 0 &&
    state.policy.feedback_days === 0 &&
    state.policy.audit_days === 0 &&
    state.policy.webhook_deliveries_days === 0;

  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <div className="mb-6 flex items-center gap-2 text-sm text-zinc-500">
        <Link href="/settings" className="inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100">
          <ArrowLeft weight="duotone" className="h-4 w-4" />
          Settings
        </Link>
      </div>

      <div className="mb-8 flex items-start gap-3">
        <HourglassMedium weight="duotone" className="mt-1 h-7 w-7 text-amber-500" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Data retention
          </h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            Cap how long this workspace keeps history, feedback, audit,
            and webhook delivery rows. Set 0 to keep forever. Reads
            filter expired rows immediately. Enforce hard deletes them.
            Cross-tenant data is never touched.
          </p>
        </div>
      </div>

      {state.kind === "loading" && (
        <div className="space-y-2" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/40" />
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <Warning weight="duotone" className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <div className="font-medium">
              {state.status === 401 ? "Invalid API key" : state.status === 403 ? "Admin role required" : "Could not load policy"}
            </div>
            <div className="mt-1 break-words">
              {state.status ? `${state.status}: ` : ""}{state.message}
            </div>
            <button className="mt-2 rounded-md border border-amber-300 px-3 py-1 text-xs hover:bg-amber-100 dark:border-amber-800 dark:hover:bg-amber-900/40" onClick={refresh}>Retry</button>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <div className="space-y-6">
          <div className={`rounded-lg border p-4 text-sm ${policyEmpty ? "border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-300" : "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-200"}`}>
            <div className="flex items-center gap-2 font-medium">
              {policyEmpty ? (
                <><Warning weight="duotone" className="h-5 w-5" />No retention policy</>
              ) : (
                <><CheckCircle weight="duotone" className="h-5 w-5" />Policy active</>
              )}
            </div>
            <div className="mt-1 text-xs opacity-80">
              tenant {state.policy.tenant_id} / updated {formatTs(state.policy.updated_at)} by{" "}
              <span className="font-mono">{state.policy.updated_by || "\u2014"}</span>
            </div>
          </div>

          <section className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Time to live, in days</h2>
            <p className="mt-1 text-xs text-zinc-500">0 keeps forever. Maximum 3650 days (10 years). Admin role plus MFA required to save.</p>
            <div className="mt-4 space-y-4">
              {CATEGORIES.map((c) => (
                <label key={c.key} className="block">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">{c.label}</span>
                    <span className="text-xs text-zinc-500">days</span>
                  </div>
                  <input
                    className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm tabular-nums dark:border-zinc-700 dark:bg-zinc-900"
                    type="number" min={0} max={3650} step={1} inputMode="numeric"
                    value={draft[c.key]}
                    onChange={(e) => setDraft((d) => ({ ...d, [c.key]: e.target.value }))}
                  />
                  <p className="mt-1 text-xs text-zinc-500">{c.hint}</p>
                </label>
              ))}

              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                MFA code
                <input
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
                  placeholder="123456" value={mfa} onChange={(e) => setMfa(e.target.value)}
                  inputMode="numeric" autoComplete="one-time-code"
                />
              </label>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                  onClick={savePolicy} disabled={busy}>
                  <FloppyDisk weight="duotone" className="h-4 w-4" />
                  {busy ? "Working..." : "Save policy"}
                </button>
                <button
                  className="inline-flex items-center gap-2 rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900"
                  onClick={() => runEnforce(true)} disabled={busy}
                  title="Count rows that would be deleted without touching disk">
                  <Eye weight="duotone" className="h-4 w-4" />Preview
                </button>
                <button
                  className="inline-flex items-center gap-2 rounded-md border border-rose-300 bg-rose-50 px-4 py-2 text-sm font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-200 dark:hover:bg-rose-950/60"
                  onClick={() => { if (window.confirm("Hard delete every row older than the policy? This cannot be undone.")) { runEnforce(false); } }}
                  disabled={busy || policyEmpty}
                  title={policyEmpty ? "Set a non-zero TTL first" : "Hard delete expired rows now"}>
                  <Broom weight="duotone" className="h-4 w-4" />Enforce now
                </button>
              </div>

              {actionError && (
                <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-200">{actionError}</div>
              )}
              {flash && (
                <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-200">{flash}</div>
              )}
              {preview && (
                <div className="rounded-md border border-sky-300 bg-sky-50 p-3 text-xs text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200">
                  <div className="font-medium">Preview (no rows deleted)</div>
                  <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
                    {CATEGORIES.map((c) => (
                      <li key={c.key} className="flex items-center justify-between gap-2">
                        <span>{c.label}</span>
                        <span className="font-mono tabular-nums">{preview.removed[c.removedKey] ?? 0}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 opacity-70">ran {formatTs(preview.ran_at)}</div>
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
