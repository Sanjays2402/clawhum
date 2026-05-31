"use client";

/**
 * Active sessions and session policy.
 *
 * Surfaces every authenticated session the workspace has produced
 * (one row per actor x IP x user agent), lets an admin force-logout
 * a single session or every session for an actor, and exposes the
 * three policy knobs enterprise security teams ask for during
 * procurement: idle timeout, absolute session lifetime, and the
 * maximum lifetime a freshly minted PAT may carry.
 *
 * Every call goes through the existing X-API-Key transport so the
 * page inherits tenant scoping for free: a leaked key cannot list
 * sessions belonging to another workspace.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Clock,
  ShieldCheck,
  ShieldWarning,
  ArrowLeft,
  Power,
  Warning,
  Pulse,
  IdentificationCard,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey } from "@/lib/apiKey";

interface SessionRow {
  id: string;
  actor: string;
  actor_kind: string;
  ip: string;
  ua_label: string;
  first_seen: number;
  last_seen: number;
  request_count: number;
  revoked: boolean;
  revoked_at: number;
  revoke_reason: string;
  is_current: boolean;
}

interface SessionList {
  items: SessionRow[];
  current_session_id: string;
}

interface PolicyRow {
  tenant_id: string;
  idle_timeout_minutes: number;
  absolute_max_minutes: number;
  max_pat_lifetime_minutes: number;
  max_pat_age_minutes: number;
  max_pat_idle_minutes: number;
  updated_at: number;
}

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; data: SessionList }
  | { kind: "error"; status: number; message: string };

type PolicyState =
  | { kind: "loading" }
  | { kind: "ready"; data: PolicyRow }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function timeAgo(ts: number): string {
  if (!ts) return "never";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

async function readError(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (body?.detail) return String(body.detail);
  } catch {}
  return r.statusText || `HTTP ${r.status}`;
}

export default function SessionsPage() {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [policy, setPolicy] = useState<PolicyState>({ kind: "loading" });
  const [mfa, setMfa] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [draft, setDraft] = useState({ idle: 0, absolute: 0, patCap: 0, patAge: 0, patIdle: 0 });

  const refreshList = useCallback(async () => {
    setList({ kind: "loading" });
    try {
      const r = await fetch("/api/sessions", { headers: { ...authHeaders() } });
      if (!r.ok) {
        setList({ kind: "error", status: r.status, message: await readError(r) });
        return;
      }
      const data = (await r.json()) as SessionList;
      setList({ kind: "ready", data });
    } catch (e: unknown) {
      setList({ kind: "error", status: 0, message: e instanceof Error ? e.message : "network error" });
    }
  }, []);

  const refreshPolicy = useCallback(async () => {
    setPolicy({ kind: "loading" });
    try {
      const r = await fetch("/api/sessions/policy", { headers: { ...authHeaders() } });
      if (!r.ok) {
        setPolicy({ kind: "error", status: r.status, message: await readError(r) });
        return;
      }
      const data = (await r.json()) as PolicyRow;
      setPolicy({ kind: "ready", data });
      setDraft({
        idle: data.idle_timeout_minutes,
        absolute: data.absolute_max_minutes,
        patCap: data.max_pat_lifetime_minutes,
        patAge: data.max_pat_age_minutes,
        patIdle: data.max_pat_idle_minutes,
      });
    } catch (e: unknown) {
      setPolicy({ kind: "error", status: 0, message: e instanceof Error ? e.message : "network error" });
    }
  }, []);

  useEffect(() => {
    refreshList();
    refreshPolicy();
  }, [refreshList, refreshPolicy]);

  const savePolicy = useCallback(async () => {
    setBusy("policy");
    setBanner(null);
    try {
      const r = await fetch("/api/sessions/policy", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(),
          ...(mfa ? { "X-MFA-Code": mfa } : {}),
        },
        body: JSON.stringify({
          idle_timeout_minutes: Math.max(0, Math.floor(draft.idle)),
          absolute_max_minutes: Math.max(0, Math.floor(draft.absolute)),
          max_pat_lifetime_minutes: Math.max(0, Math.floor(draft.patCap)),
          max_pat_age_minutes: Math.max(0, Math.floor(draft.patAge)),
          max_pat_idle_minutes: Math.max(0, Math.floor(draft.patIdle)),
        }),
      });
      if (!r.ok) {
        setBanner({ kind: "err", msg: await readError(r) });
        return;
      }
      setBanner({ kind: "ok", msg: "Policy saved." });
      await refreshPolicy();
    } finally {
      setBusy(null);
    }
  }, [draft, mfa, refreshPolicy]);

  const revokeOne = useCallback(
    async (id: string) => {
      setBusy(id);
      setBanner(null);
      try {
        const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
          method: "DELETE",
          headers: {
            ...authHeaders(),
            ...(mfa ? { "X-MFA-Code": mfa } : {}),
          },
        });
        if (!r.ok && r.status !== 204) {
          setBanner({ kind: "err", msg: await readError(r) });
          return;
        }
        setBanner({ kind: "ok", msg: "Session revoked." });
        await refreshList();
      } finally {
        setBusy(null);
      }
    },
    [mfa, refreshList],
  );

  const revokeActor = useCallback(
    async (actor: string) => {
      setBusy(`actor:${actor}`);
      setBanner(null);
      try {
        const r = await fetch("/api/sessions/revoke-all", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders(),
            ...(mfa ? { "X-MFA-Code": mfa } : {}),
          },
          body: JSON.stringify({ actor, reason: "ui force logout", include_self: false }),
        });
        if (!r.ok) {
          setBanner({ kind: "err", msg: await readError(r) });
          return;
        }
        const body = (await r.json()) as { revoked: number };
        setBanner({ kind: "ok", msg: `Revoked ${body.revoked} session${body.revoked === 1 ? "" : "s"}.` });
        await refreshList();
      } finally {
        setBusy(null);
      }
    },
    [mfa, refreshList],
  );

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-900"
      >
        <ArrowLeft size={14} weight="duotone" />
        Settings
      </Link>

      <header className="mt-4 flex items-center gap-3">
        <div className="rounded-lg bg-neutral-100 p-2">
          <Pulse size={20} weight="duotone" className="text-neutral-700" />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Active sessions</h1>
          <p className="mt-0.5 text-sm text-neutral-500">
            See every authenticated client, force log them out, and pin a
            workspace session policy.
          </p>
        </div>
      </header>

      {banner ? (
        <div
          className={`mt-4 rounded-md border px-3 py-2 text-sm ${
            banner.kind === "ok"
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border-red-200 bg-red-50 text-red-900"
          }`}
        >
          {banner.msg}
        </div>
      ) : null}

      <section className="mt-6 rounded-xl border border-neutral-200 bg-white">
        <div className="flex items-center justify-between gap-3 border-b border-neutral-100 px-4 py-3 sm:px-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={16} weight="duotone" className="text-neutral-600" />
            <h2 className="text-sm font-medium">Session policy</h2>
          </div>
          <div className="text-xs text-neutral-500">
            {policy.kind === "ready" && policy.data.updated_at
              ? `updated ${timeAgo(policy.data.updated_at)}`
              : "not set"}
          </div>
        </div>
        <div className="px-4 py-4 sm:px-5">
          {policy.kind === "loading" ? (
            <div className="h-16 animate-pulse rounded bg-neutral-100" />
          ) : policy.kind === "error" ? (
            <div className="flex items-start gap-2 text-sm text-red-700">
              <Warning size={16} weight="duotone" />
              <span>Could not load policy: {policy.message}</span>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-5">
              <label className="block">
                <span className="text-xs font-medium text-neutral-700">
                  Idle timeout (minutes)
                </span>
                <input
                  type="number"
                  min={0}
                  value={draft.idle}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, idle: Number(e.target.value) }))
                  }
                  className="mt-1 block w-full rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm focus:border-neutral-400 focus:outline-none"
                />
                <span className="mt-1 block text-[11px] text-neutral-500">
                  0 = no idle timeout
                </span>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-neutral-700">
                  Absolute lifetime (minutes)
                </span>
                <input
                  type="number"
                  min={0}
                  value={draft.absolute}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, absolute: Number(e.target.value) }))
                  }
                  className="mt-1 block w-full rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm focus:border-neutral-400 focus:outline-none"
                />
                <span className="mt-1 block text-[11px] text-neutral-500">
                  0 = no cap
                </span>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-neutral-700">
                  Max PAT lifetime (minutes)
                </span>
                <input
                  type="number"
                  min={0}
                  value={draft.patCap}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, patCap: Number(e.target.value) }))
                  }
                  className="mt-1 block w-full rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm focus:border-neutral-400 focus:outline-none"
                />
                <span className="mt-1 block text-[11px] text-neutral-500">
                  0 = use global default
                </span>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-neutral-700">
                  Force PAT rotation after (minutes)
                </span>
                <input
                  type="number"
                  min={0}
                  value={draft.patAge}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, patAge: Number(e.target.value) }))
                  }
                  className="mt-1 block w-full rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm focus:border-neutral-400 focus:outline-none"
                />
                <span className="mt-1 block text-[11px] text-neutral-500">
                  0 = never force rotation. Aged tokens return HTTP 401.
                </span>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-neutral-700">
                  Revoke PAT if unused for (minutes)
                </span>
                <input
                  type="number"
                  min={0}
                  value={draft.patIdle}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, patIdle: Number(e.target.value) }))
                  }
                  className="mt-1 block w-full rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm focus:border-neutral-400 focus:outline-none"
                />
                <span className="mt-1 block text-[11px] text-neutral-500">
                  0 = never revoke. Idle tokens return HTTP 401.
                </span>
              </label>
              <div className="sm:col-span-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <label className="block sm:max-w-xs">
                  <span className="text-xs font-medium text-neutral-700">
                    MFA code (if enrolled)
                  </span>
                  <input
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={mfa}
                    onChange={(e) => setMfa(e.target.value.trim())}
                    placeholder="123456"
                    className="mt-1 block w-full rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm focus:border-neutral-400 focus:outline-none"
                  />
                </label>
                <button
                  type="button"
                  onClick={savePolicy}
                  disabled={busy === "policy"}
                  className="inline-flex items-center justify-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
                >
                  <ShieldCheck size={14} weight="duotone" />
                  {busy === "policy" ? "Saving" : "Save policy"}
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="mt-6 rounded-xl border border-neutral-200 bg-white">
        <div className="flex items-center justify-between gap-3 border-b border-neutral-100 px-4 py-3 sm:px-5">
          <div className="flex items-center gap-2">
            <IdentificationCard size={16} weight="duotone" className="text-neutral-600" />
            <h2 className="text-sm font-medium">Sessions</h2>
          </div>
          <button
            type="button"
            onClick={refreshList}
            className="text-xs text-neutral-500 hover:text-neutral-900"
          >
            Refresh
          </button>
        </div>
        {list.kind === "loading" ? (
          <div className="space-y-2 px-4 py-4 sm:px-5">
            <div className="h-10 animate-pulse rounded bg-neutral-100" />
            <div className="h-10 animate-pulse rounded bg-neutral-100" />
          </div>
        ) : list.kind === "error" ? (
          <div className="flex items-start gap-2 px-4 py-6 text-sm text-red-700 sm:px-5">
            <ShieldWarning size={16} weight="duotone" />
            <span>
              {list.status === 403
                ? "You need the admin role to view sessions."
                : list.message}
            </span>
          </div>
        ) : list.data.items.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-neutral-500 sm:px-5">
            No active sessions yet. They appear as soon as any credential
            hits an authenticated route.
          </div>
        ) : (
          <ul className="divide-y divide-neutral-100">
            {list.data.items.map((s) => (
              <li key={s.id} className="px-4 py-3 sm:px-5">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium text-neutral-900">{s.actor}</span>
                      <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] uppercase tracking-wide text-neutral-600">
                        {s.actor_kind}
                      </span>
                      {s.is_current ? (
                        <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-700">
                          this session
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-neutral-500">
                      <span>IP {s.ip || "unknown"}</span>
                      <span className="truncate max-w-[260px]" title={s.ua_label}>
                        {s.ua_label}
                      </span>
                      <span className="inline-flex items-center gap-1">
                        <Clock size={11} weight="duotone" />
                        {timeAgo(s.last_seen)}
                      </span>
                      <span>{s.request_count} req</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={busy === s.id || s.is_current}
                      onClick={() => revokeOne(s.id)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-40"
                      title={
                        s.is_current
                          ? "Cannot revoke the current session from this row; use force logout."
                          : "Revoke this session"
                      }
                    >
                      <Power size={12} weight="duotone" />
                      Revoke
                    </button>
                    <button
                      type="button"
                      disabled={busy === `actor:${s.actor}`}
                      onClick={() => revokeActor(s.actor)}
                      className="inline-flex items-center gap-1.5 rounded-md bg-red-50 px-2.5 py-1 text-xs text-red-700 hover:bg-red-100 disabled:opacity-50"
                      title="Force log out every session for this actor"
                    >
                      <ShieldWarning size={12} weight="duotone" />
                      Force logout
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="mt-4 text-xs text-neutral-500">
        Sessions are scoped to your workspace. Policy changes are audited and
        require a fresh <code className="rounded bg-neutral-100 px-1">X-MFA-Code</code>{" "}
        once MFA is enrolled.
      </p>
    </div>
  );
}
