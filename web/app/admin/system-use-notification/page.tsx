"use client";

/**
 * Workspace system use notification console.
 *
 * NIST 800-53 AC-8 and the SOC2 / FedRAMP equivalents require a
 * "system use notification" before a user is allowed to act on the
 * system. This screen lets a workspace admin write the banner text,
 * toggle enforcement, and review who has acknowledged the current
 * revision. Wording changes bump the revision number and invalidate
 * every existing acknowledgement, forcing a fresh ack campaign.
 *
 * Strictly per workspace; the backend enforces tenant scoping.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle,
  Megaphone,
  ShieldCheck,
  Warning,
} from "@phosphor-icons/react/dist/ssr";
import { API_BASE } from "@/lib/api";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface BannerView {
  enforced: boolean;
  revision: number;
  title: string;
  body: string;
  updated_at: number;
  updated_by: string;
  needs_ack: boolean;
  actor_id: string;
  actor_acked_revision: number;
}

interface AckRow {
  actor_id: string;
  revision: number;
  acked_at: number;
  ip: string;
}

const MAX_TITLE = 200;
const MAX_BODY = 8000;

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

export default function SystemUseNotificationPage() {
  useApiKey();
  const [data, setData] = useState<BannerView | null>(null);
  const [acks, setAcks] = useState<AckRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [enforced, setEnforced] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(API_BASE + "/system-use-notification", {
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as BannerView;
      setData(j);
      setTitle(j.title);
      setBody(j.body);
      setEnforced(j.enforced || j.revision === 0);

      const ar = await fetch(API_BASE + "/system-use-notification/acks", {
        headers: authHeaders(),
      });
      if (ar.ok) {
        setAcks((await ar.json()) as AckRow[]);
      } else if (ar.status === 403) {
        setAcks(null);
      } else {
        setAcks([]);
      }
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
      const r = await fetch(API_BASE + "/system-use-notification", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ title, body, enforced }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) {
        const detail = j?.detail?.message || j?.detail || `HTTP ${r.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      const next = j as BannerView;
      setData(next);
      setTitle(next.title);
      setBody(next.body);
      setEnforced(next.enforced);
      setMsg(`Saved at revision ${next.revision}`);
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }, [title, body, enforced]);

  const ack = useCallback(async () => {
    if (!data) return;
    setBusy(true);
    setMsg(null);
    setActionErr(null);
    try {
      const r = await fetch(API_BASE + "/system-use-notification/ack", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ revision: data.revision }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) {
        const detail = j?.detail?.message || j?.detail || `HTTP ${r.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setMsg(`Acknowledged revision ${data.revision}`);
      await load();
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : "ack failed");
    } finally {
      setBusy(false);
    }
  }, [data, load]);

  const dirty = useMemo(() => {
    if (!data) return false;
    return (
      title.trim() !== data.title.trim() ||
      body.trim() !== data.body.trim() ||
      enforced !== data.enforced
    );
  }, [data, title, body, enforced]);

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
          <Megaphone size={14} weight="duotone" />
          System use notification
        </div>
        <h1 className="text-2xl font-semibold sm:text-3xl">Workspace login banner</h1>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-dim)]">
          Every actor in this workspace must acknowledge the banner
          below before mutating actions are accepted. Changing the
          wording bumps the revision number and invalidates every
          prior acknowledgement, forcing a fresh ack across the
          workspace. Reader calls and the ack endpoint itself are
          never blocked.
        </p>
      </header>

      {loading ? (
        <div className="space-y-3" aria-busy="true">
          <div className="h-24 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
          <div className="h-56 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/5 p-5 text-sm text-rose-300">
          <div className="mb-1 flex items-center gap-2 font-medium">
            <Warning size={16} weight="duotone" /> Could not load banner
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
          No data. Try reloading.
        </div>
      ) : (
        <div className="space-y-6">
          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-xs uppercase tracking-wider text-[var(--color-dim)]">
                  Current revision
                </div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">
                  rev {data.revision || 0}
                  <span className="ml-2 text-xs font-normal text-[var(--color-dim)]">
                    {data.revision === 0
                      ? "no banner configured"
                      : data.enforced
                      ? "enforced"
                      : "paused"}
                  </span>
                </div>
                <div className="mt-1 text-xs text-[var(--color-dim)]">
                  You ({data.actor_id}) acked rev {data.actor_acked_revision}
                  {data.needs_ack ? " — acknowledgement required" : ""}
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
            {data.needs_ack && (
              <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-200">
                <div className="mb-2 font-medium">{data.title}</div>
                <p className="mb-3 whitespace-pre-wrap text-amber-100/80">
                  {data.body}
                </p>
                <button
                  type="button"
                  onClick={ack}
                  disabled={busy}
                  className="inline-flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium hover:bg-amber-500/20 focus:outline-none focus:ring-2 focus:ring-amber-400 disabled:opacity-50"
                >
                  <CheckCircle size={14} weight="duotone" />
                  Acknowledge revision {data.revision}
                </button>
              </div>
            )}
          </section>

          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h2 className="text-sm font-semibold">Banner text</h2>
            <p className="mt-1 text-xs text-[var(--color-dim)]">
              MFA step up required to save. Empty text disables the
              gate even if enforcement is on, so you can stage wording.
            </p>
            <div className="mt-4 space-y-3">
              <div>
                <label
                  htmlFor="sun-title"
                  className="mb-1 block text-xs uppercase tracking-wider text-[var(--color-dim)]"
                >
                  Title
                </label>
                <input
                  id="sun-title"
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={MAX_TITLE}
                  disabled={busy}
                  className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40"
                  placeholder="Authorized use only"
                />
                <div className="mt-1 text-right text-[10px] text-[var(--color-dim)]">
                  {title.length} / {MAX_TITLE}
                </div>
              </div>
              <div>
                <label
                  htmlFor="sun-body"
                  className="mb-1 block text-xs uppercase tracking-wider text-[var(--color-dim)]"
                >
                  Body
                </label>
                <textarea
                  id="sun-body"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  maxLength={MAX_BODY}
                  rows={8}
                  disabled={busy}
                  className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 font-mono text-xs leading-5 focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40"
                  placeholder="By continuing you consent to monitoring of all activity. Unauthorized access is prohibited."
                />
                <div className="mt-1 text-right text-[10px] text-[var(--color-dim)]">
                  {body.length} / {MAX_BODY}
                </div>
              </div>
              <label className="flex cursor-pointer select-none items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={enforced}
                  onChange={(e) => setEnforced(e.target.checked)}
                  disabled={busy}
                  className="h-4 w-4 rounded border-[var(--color-border)] bg-[var(--color-bg)]"
                />
                Enforce on mutating routes (block POST, PUT, PATCH, DELETE
                without an ack)
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={save}
                  disabled={busy || !dirty}
                  className="inline-flex items-center gap-2 rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-2 text-sm font-medium hover:bg-[var(--color-accent)]/20 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <ShieldCheck size={14} weight="duotone" />
                  {busy ? "Saving" : "Save"}
                </button>
                {dirty && (
                  <button
                    type="button"
                    onClick={() => {
                      setTitle(data.title);
                      setBody(data.body);
                      setEnforced(data.enforced);
                      setActionErr(null);
                    }}
                    disabled={busy}
                    className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-dim)] hover:text-[var(--color-text)]"
                  >
                    Reset
                  </button>
                )}
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h2 className="text-sm font-semibold">Acknowledgements</h2>
            <p className="mt-1 text-xs text-[var(--color-dim)]">
              Latest ack per actor in this workspace. Use this as
              evidence in your SOC2 or FedRAMP package.
            </p>
            <div className="mt-4">
              {acks === null ? (
                <p className="text-xs text-[var(--color-dim)]">
                  Admin role required to view the ack roster.
                </p>
              ) : acks.length === 0 ? (
                <p className="text-xs text-[var(--color-dim)]">
                  No acks recorded yet.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-[var(--color-dim)]">
                      <tr>
                        <th className="px-2 py-1 font-medium">Actor</th>
                        <th className="px-2 py-1 font-medium">Rev</th>
                        <th className="px-2 py-1 font-medium">Acked at</th>
                        <th className="px-2 py-1 font-medium">IP</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono">
                      {acks.map((a) => (
                        <tr
                          key={`${a.actor_id}-${a.revision}-${a.acked_at}`}
                          className="border-t border-[var(--color-border)]"
                        >
                          <td className="px-2 py-1">{a.actor_id}</td>
                          <td
                            className={`px-2 py-1 tabular-nums ${
                              data.revision && a.revision < data.revision
                                ? "text-amber-400"
                                : ""
                            }`}
                          >
                            {a.revision}
                          </td>
                          <td className="px-2 py-1 text-[var(--color-dim)]">
                            {fmtDate(a.acked_at)}
                          </td>
                          <td className="px-2 py-1 text-[var(--color-dim)]">
                            {a.ip || "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
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
