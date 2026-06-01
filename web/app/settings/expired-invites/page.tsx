"use client";

/**
 * Expired pending-invite cleanup.
 *
 * Lists workspace invite tokens that have passed their expiry
 * timestamp. The tokens are already useless (the accept endpoint
 * refuses them) but the seat rows remain in the roster until an
 * admin clears them. SOC2 dormant-credentials reviews expect a
 * single place to see the backlog and clear it.
 *
 * Reader role can view, admin role can purge. A dry-run preview is
 * available so the cleanup can be scripted in CI without touching
 * production state.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Broom,
  ArrowLeft,
  Warning,
  Clock,
  CheckCircle,
  Eye,
  Envelope,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface Member {
  id: string;
  email: string;
  role: string;
  status: "invited" | "active" | "revoked";
  invited_by: string;
  invited_at: number;
  accepted_at: number;
  invite_expires_at: number;
}

interface ListResp {
  members: Member[];
  count: number;
}

interface PurgeResp {
  purged: Member[];
  count: number;
}

interface DryRunResp {
  dry_run: true;
  would_delete: {
    kind: string;
    count: number;
    would_purge: Member[];
  };
  tenant_id?: string;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ListResp }
  | { kind: "error"; status: number; message: string };

type ActionState =
  | { kind: "idle" }
  | { kind: "preview"; data: DryRunResp }
  | { kind: "purged"; data: PurgeResp }
  | { kind: "error"; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function fmtTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function fmtAge(ts: number): string {
  if (!ts) return "";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default function ExpiredInvitesPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [action, setAction] = useState<ActionState>({ kind: "idle" });
  const [busy, setBusy] = useState<"preview" | "purge" | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/members/expired-invites", {
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
      const data = (await r.json()) as ListResp;
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
  }, [refresh, storedKey]);

  async function runPreview() {
    setBusy("preview");
    setAction({ kind: "idle" });
    try {
      const r = await fetch("/api/members/expired-invites/purge?dry_run=true", {
        method: "POST",
        headers: authHeaders(),
      });
      if (!r.ok) {
        const body = await r.text();
        setAction({
          kind: "error",
          message: `Preview failed (${r.status}): ${body || r.statusText}`,
        });
        return;
      }
      const data = (await r.json()) as DryRunResp;
      setAction({ kind: "preview", data });
    } catch (err) {
      setAction({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(null);
    }
  }

  async function runPurge() {
    setBusy("purge");
    try {
      const r = await fetch("/api/members/expired-invites/purge", {
        method: "POST",
        headers: authHeaders(),
      });
      if (!r.ok) {
        const body = await r.text();
        setAction({
          kind: "error",
          message: `Purge failed (${r.status}): ${body || r.statusText}`,
        });
        return;
      }
      const data = (await r.json()) as PurgeResp;
      setAction({ kind: "purged", data });
      await refresh();
    } catch (err) {
      setAction({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(null);
    }
  }

  const count = state.kind === "ready" ? state.data.count : 0;
  const members = state.kind === "ready" ? state.data.members : [];

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        <ArrowLeft size={14} weight="duotone" />
        Back to settings
      </Link>

      <header className="mt-4 flex items-start gap-3">
        <Broom size={28} weight="duotone" className="text-amber-600 mt-1" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Expired invite cleanup
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Pending invites whose tokens have already expired stay in the
            roster until an admin clears them. Purge the backlog in one
            click, or preview the cleanup first with a dry run.
          </p>
        </div>
      </header>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-2">
          <div className="h-16 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
          <div className="h-16 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
          <Warning size={18} weight="duotone" className="mt-0.5" />
          <div>
            <div className="font-medium">
              Could not load expired invites ({state.status || "network"})
            </div>
            <div className="mt-1 break-all">{state.message}</div>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <section className="mt-8 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-2 text-sm">
                {count === 0 ? (
                  <CheckCircle size={16} weight="duotone" className="text-emerald-600" />
                ) : (
                  <Clock size={16} weight="duotone" className="text-amber-600" />
                )}
                <span className="font-medium">
                  {count === 0
                    ? "No expired invites"
                    : `${count} expired invite${count === 1 ? "" : "s"} pending cleanup`}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={runPreview}
                  disabled={busy !== null || count === 0}
                  className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-900"
                >
                  <Eye size={14} weight="duotone" />
                  {busy === "preview" ? "Previewing..." : "Dry run"}
                </button>
                <button
                  onClick={runPurge}
                  disabled={busy !== null || count === 0}
                  className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-100"
                >
                  <Broom size={14} weight="duotone" />
                  {busy === "purge" ? "Purging..." : "Purge all"}
                </button>
              </div>
            </div>
          </section>

          {action.kind === "preview" && (
            <section className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/20 dark:text-amber-200">
              <div className="font-medium">
                Dry run: {action.data.would_delete.count} row{action.data.would_delete.count === 1 ? "" : "s"} would be purged.
              </div>
              <div className="mt-1 text-xs text-amber-800/80 dark:text-amber-300/80">
                Nothing was changed. Click Purge all to apply.
              </div>
            </section>
          )}

          {action.kind === "purged" && (
            <section className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/20 dark:text-emerald-200">
              <div className="font-medium">
                Purged {action.data.count} expired invite{action.data.count === 1 ? "" : "s"}.
              </div>
            </section>
          )}

          {action.kind === "error" && (
            <section className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              <div className="font-medium">Could not complete action</div>
              <div className="mt-1 break-all">{action.message}</div>
            </section>
          )}

          <section className="mt-6 rounded-lg border border-zinc-200 dark:border-zinc-800">
            <h2 className="px-6 py-4 text-sm font-semibold border-b border-zinc-200 dark:border-zinc-800">
              Backlog
            </h2>
            {members.length === 0 ? (
              <div className="px-6 py-10 text-center text-sm text-zinc-500">
                <CheckCircle size={20} weight="duotone" className="mx-auto mb-2 text-emerald-600" />
                Nothing to clean up. Every pending invite is still within its expiry window.
              </div>
            ) : (
              <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {members.map((m) => (
                  <li key={m.id} className="flex flex-col gap-1 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-2 text-sm">
                      <Envelope size={14} weight="duotone" className="text-zinc-400" />
                      <span className="font-medium truncate">{m.email}</span>
                      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                        {m.role}
                      </span>
                    </div>
                    <div className="text-xs text-zinc-500">
                      Invited by {m.invited_by || "unknown"} {fmtAge(m.invited_at)} <span className="mx-1">|</span> expired {fmtTs(m.invite_expires_at)}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </main>
  );
}
