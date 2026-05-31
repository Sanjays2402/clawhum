"use client";

/**
 * Workspace Data Processing Agreement (DPA) acceptance console.
 *
 * What an enterprise security reviewer expects to see here:
 *
 *   - The current DPA version published by the vendor.
 *   - Whether their workspace has accepted it, who clicked accept,
 *     when, and from which IP and user agent.
 *   - A one-click accept (admin + MFA) and withdraw (admin + MFA).
 *
 * Strictly read your own workspace; the backend enforces tenant
 * scoping. Mutations flow through the existing audit chain.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  FileText,
  CheckCircle,
  XCircle,
  ArrowSquareOut,
  Warning,
  ShieldCheck,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface Acceptance {
  version: string;
  accepted_by: string;
  accepted_at: number;
  ip: string;
  user_agent: string;
}

interface Status {
  current_version: string;
  current_url: string;
  accepted: boolean;
  acceptance: Acceptance | null;
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

export default function DpaPage() {
  useApiKey();
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "accept" | "withdraw">("");
  const [msg, setMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/dpa", { headers: authHeaders(), cache: "no-store" });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      setStatus((await r.json()) as Status);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const accept = useCallback(async () => {
    if (!status) return;
    setBusy("accept");
    setActionErr(null);
    setMsg(null);
    try {
      const r = await fetch("/api/dpa/accept", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ version: status.current_version }),
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      setMsg("Accepted. The record is in your audit log.");
      await refresh();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }, [status, refresh]);

  const withdraw = useCallback(async () => {
    setBusy("withdraw");
    setActionErr(null);
    setMsg(null);
    try {
      const r = await fetch("/api/dpa", {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!r.ok && r.status !== 204) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      setMsg("Withdrawn. Re-accept the current version when ready.");
      await refresh();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }, [refresh]);

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
            <FileText size={18} weight="duotone" />
            <h1 className="text-lg font-medium">data processing agreement</h1>
          </div>
          <p className="mt-2 text-sm text-[var(--color-dim)]">
            Record the workspace acceptance of the vendor DPA. Required
            for GDPR Article 28 and most enterprise procurement checks.
            Acceptance is admin only and logged in the audit chain.
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
              <span>Could not load DPA status.</span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap text-xs opacity-80">{error}</pre>
          </div>
        ) : status ? (
          <div className="space-y-6">
            <section className="rounded-md border border-[var(--color-border)] p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-wide text-[var(--color-dim)]">
                    current vendor version
                  </div>
                  <div className="mt-1 font-mono text-sm">{status.current_version}</div>
                </div>
                <a
                  href={status.current_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-xs hover:bg-[var(--color-border)]/30"
                >
                  read agreement
                  <ArrowSquareOut size={12} weight="duotone" />
                </a>
              </div>

              <div className="mt-5 flex items-center gap-2 text-sm">
                {status.accepted ? (
                  <>
                    <CheckCircle size={16} weight="duotone" className="text-emerald-400" />
                    <span>Accepted by this workspace.</span>
                  </>
                ) : (
                  <>
                    <XCircle size={16} weight="duotone" className="text-amber-400" />
                    <span>Not yet accepted by this workspace.</span>
                  </>
                )}
              </div>
            </section>

            {status.acceptance ? (
              <section className="rounded-md border border-[var(--color-border)] p-5">
                <div className="text-xs uppercase tracking-wide text-[var(--color-dim)]">
                  acceptance record
                </div>
                <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-[160px_1fr]">
                  <dt className="text-[var(--color-dim)]">version</dt>
                  <dd className="font-mono">{status.acceptance.version}</dd>
                  <dt className="text-[var(--color-dim)]">accepted by</dt>
                  <dd className="font-mono break-all">{status.acceptance.accepted_by}</dd>
                  <dt className="text-[var(--color-dim)]">accepted at</dt>
                  <dd className="font-mono">{fmtDate(status.acceptance.accepted_at)}</dd>
                  <dt className="text-[var(--color-dim)]">source ip</dt>
                  <dd className="font-mono">{status.acceptance.ip || "(unknown)"}</dd>
                  <dt className="text-[var(--color-dim)]">user agent</dt>
                  <dd className="font-mono break-all text-xs">
                    {status.acceptance.user_agent || "(unknown)"}
                  </dd>
                </dl>
              </section>
            ) : (
              <section className="rounded-md border border-dashed border-[var(--color-border)] p-5 text-sm text-[var(--color-dim)]">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={14} weight="duotone" />
                  <span>No acceptance on file for this workspace yet.</span>
                </div>
              </section>
            )}

            <section className="rounded-md border border-[var(--color-border)] p-5">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={accept}
                  disabled={
                    busy === "accept" ||
                    (status.accepted &&
                      status.acceptance?.version === status.current_version)
                  }
                  className="inline-flex items-center gap-2 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <CheckCircle size={14} weight="duotone" />
                  {status.accepted &&
                  status.acceptance?.version === status.current_version
                    ? "already accepted"
                    : busy === "accept"
                    ? "accepting"
                    : "accept current version"}
                </button>

                {status.accepted ? (
                  <button
                    type="button"
                    onClick={withdraw}
                    disabled={busy === "withdraw"}
                    className="inline-flex items-center gap-2 rounded border border-[var(--color-border)] px-3 py-1.5 text-sm hover:bg-[var(--color-border)]/30 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <XCircle size={14} weight="duotone" />
                    {busy === "withdraw" ? "withdrawing" : "withdraw acceptance"}
                  </button>
                ) : null}
              </div>

              <p className="mt-3 text-xs text-[var(--color-dim)]">
                Both actions require the admin role and a fresh MFA code.
                Every mutation is recorded in the audit chain with method,
                path, actor, ip, and request id.
              </p>

              {msg ? (
                <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/5 p-2 text-xs text-emerald-200">
                  {msg}
                </div>
              ) : null}
              {actionErr ? (
                <div className="mt-3 rounded border border-rose-500/30 bg-rose-500/5 p-2 text-xs text-rose-300">
                  {actionErr}
                </div>
              ) : null}
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
