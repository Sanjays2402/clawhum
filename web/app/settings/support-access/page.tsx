"use client";

/**
 * Per-workspace vendor support access grants.
 *
 * Admins approve named clawhum support staff (identified by email)
 * to act on the workspace for a bounded window with either read or
 * write scope. Without an active grant, any inbound request that
 * carries X-Support-Actor is rejected 403 at the auth layer. With an
 * active grant, every mutating action is recorded in the audit log
 * with the support actor and grant id, which is the forensic
 * evidence buyers ask for during SOC2 and ISO 27001 reviews.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Headset,
  Plus,
  Prohibit,
  Warning,
  Clock,
  Eye,
  PencilSimple,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface GrantRow {
  id: string;
  tenant_id: string;
  support_actor: string;
  scope: string;
  reason: string;
  created_at: number;
  expires_at: number;
  created_by: string;
  active: boolean;
  revoked_at: number | null;
  revoked_by: string | null;
  revoke_reason: string | null;
}

interface ListResp {
  grants: GrantRow[];
  active_count: number;
  max_ttl_seconds: number;
  default_ttl_seconds: number;
  allowed_scopes: string[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ListResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

function formatRemaining(expires: number, now: number): string {
  const sec = Math.max(0, expires - now);
  if (sec >= 86400) return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h left`;
  if (sec >= 3600) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m left`;
  if (sec >= 60) return `${Math.floor(sec / 60)}m left`;
  return `${Math.floor(sec)}s left`;
}

const TTL_PRESETS = [
  { label: "1 hour", sec: 3600 },
  { label: "8 hours", sec: 8 * 3600 },
  { label: "24 hours", sec: 24 * 3600 },
  { label: "3 days", sec: 3 * 24 * 3600 },
  { label: "7 days", sec: 7 * 24 * 3600 },
];

export default function SupportAccessPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [actor, setActor] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [ttlSec, setTtlSec] = useState(24 * 3600);
  const [reason, setReason] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));

  useEffect(() => {
    const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(t);
  }, []);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/support-grants", { headers: authHeaders() });
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

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!actor.trim() || !reason.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/support-grants", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          support_actor: actor.trim(),
          scope,
          reason: reason.trim(),
          ttl_seconds: ttlSec,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setCreateError(
          (body && typeof body.detail === "string" && body.detail) ||
            `request failed (${r.status})`
        );
        return;
      }
      setActor("");
      setReason("");
      setScope("read");
      setTtlSec(24 * 3600);
      await refresh();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function onRevoke(id: string) {
    setBusyId(id);
    try {
      const r = await fetch(`/api/support-grants/${id}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ reason: revokeReason.trim() }),
      });
      if (!r.ok) {
        const body = await r.text();
        alert(`revoke failed: ${body || r.statusText}`);
        return;
      }
      setConfirmId(null);
      setRevokeReason("");
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  const ready = state.kind === "ready" ? state.data : null;
  const activeGrants = useMemo(
    () => (ready ? ready.grants.filter((g) => g.active) : []),
    [ready]
  );
  const historicalGrants = useMemo(
    () => (ready ? ready.grants.filter((g) => !g.active) : []),
    [ready]
  );

  return (
    <main className="min-h-dvh bg-[var(--color-bg)] px-4 py-8 md:px-12 md:py-12 text-[var(--color-fg)]">
      <div className="mx-auto max-w-3xl space-y-6">
        <header className="flex items-start gap-3">
          <Link
            href="/settings"
            className="mt-1 inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
          >
            <ArrowLeft size={14} weight="duotone" />
            settings
          </Link>
          <div className="ml-auto" />
        </header>

        <section className="panel rounded-[2px] p-4 md:p-6 space-y-3">
          <div className="flex items-center gap-2">
            <Headset size={18} weight="duotone" className="text-[var(--color-phosphor)]" />
            <h1 className="font-mono text-[13px] uppercase tracking-widest">support access grants</h1>
            {ready ? (
              <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
                {ready.active_count} active / {ready.grants.length} total
              </span>
            ) : null}
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            grant named clawhum support staff temporary access to your workspace. without an active grant, requests carrying X-Support-Actor are rejected 403. every action under a grant is recorded in the audit log with the support actor email and grant id. hard cap on any single grant is 7 days; revoke instantly when the incident closes.
          </p>
        </section>

        <section className="panel rounded-[2px] p-4 md:p-6 space-y-3">
          <div className="flex items-center gap-2">
            <Plus size={14} weight="duotone" />
            <h2 className="font-mono text-[11px] uppercase tracking-widest">new grant</h2>
          </div>
          <form onSubmit={onCreate} className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block space-y-1">
                <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">support actor email</span>
                <input
                  type="email"
                  required
                  value={actor}
                  onChange={(e) => setActor(e.target.value)}
                  placeholder="alice@clawhum.com"
                  className="w-full border border-[var(--color-line)] bg-transparent px-3 py-2 font-mono text-[12px] focus:outline-none focus:border-[var(--color-phosphor)]"
                />
              </label>
              <div className="space-y-1">
                <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">scope</span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setScope("read")}
                    aria-pressed={scope === "read"}
                    className={`flex-1 inline-flex items-center justify-center gap-2 border px-3 py-2 font-mono text-[11px] uppercase tracking-widest ${scope === "read" ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)]" : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-fg)]"}`}
                  >
                    <Eye size={12} weight="duotone" /> read
                  </button>
                  <button
                    type="button"
                    onClick={() => setScope("write")}
                    aria-pressed={scope === "write"}
                    className={`flex-1 inline-flex items-center justify-center gap-2 border px-3 py-2 font-mono text-[11px] uppercase tracking-widest ${scope === "write" ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)]" : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-fg)]"}`}
                  >
                    <PencilSimple size={12} weight="duotone" /> write
                  </button>
                </div>
              </div>
            </div>
            <div className="space-y-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">duration</span>
              <div className="flex flex-wrap gap-2">
                {TTL_PRESETS.map((p) => (
                  <button
                    type="button"
                    key={p.sec}
                    onClick={() => setTtlSec(p.sec)}
                    aria-pressed={ttlSec === p.sec}
                    className={`border px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest ${ttlSec === p.sec ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)]" : "border-[var(--color-line)] text-[var(--color-muted)] hover:text-[var(--color-fg)]"}`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
            <label className="block space-y-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">reason (lands in audit log)</span>
              <textarea
                required
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Debug failing match runs for incident #1234"
                rows={2}
                maxLength={500}
                className="w-full border border-[var(--color-line)] bg-transparent px-3 py-2 font-mono text-[12px] focus:outline-none focus:border-[var(--color-phosphor)]"
              />
            </label>
            {createError ? (
              <div className="inline-flex items-start gap-2 border border-[var(--color-line)] px-3 py-2 font-mono text-[11px] text-[var(--color-phosphor)]">
                <Warning size={14} weight="duotone" />
                <span>{createError}</span>
              </div>
            ) : null}
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={creating || !actor.trim() || !reason.trim()}
                className="inline-flex items-center gap-2 border border-[var(--color-phosphor)] px-4 py-2 font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-phosphor)] hover:text-[var(--color-bg)] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Plus size={12} weight="duotone" />
                {creating ? "granting..." : "grant access"}
              </button>
              <span className="font-mono text-[10px] text-[var(--color-dim)]">requires admin role and a fresh MFA code</span>
            </div>
          </form>
        </section>

        <section className="panel rounded-[2px] p-4 md:p-6 space-y-3">
          <div className="flex items-center gap-2">
            <Clock size={14} weight="duotone" />
            <h2 className="font-mono text-[11px] uppercase tracking-widest">active grants</h2>
          </div>
          {state.kind === "loading" ? (
            <div className="space-y-2" aria-busy="true">
              <div className="h-12 animate-pulse bg-[var(--color-line)]/40" />
              <div className="h-12 animate-pulse bg-[var(--color-line)]/40" />
            </div>
          ) : state.kind === "error" ? (
            <div className="inline-flex items-start gap-2 border border-[var(--color-line)] px-3 py-2 font-mono text-[11px] text-[var(--color-phosphor)]">
              <Warning size={14} weight="duotone" />
              <span>{state.message || `status ${state.status}`}</span>
            </div>
          ) : activeGrants.length === 0 ? (
            <p className="font-mono text-[11px] text-[var(--color-dim)]">no active grants. support staff cannot touch this workspace.</p>
          ) : (
            <ul className="divide-y divide-[var(--color-line)]">
              {activeGrants.map((g) => (
                <li key={g.id} className="py-3 space-y-2">
                  <div className="flex flex-col md:flex-row md:items-center md:gap-3">
                    <div className="flex items-center gap-2 min-w-0">
                      {g.scope === "write" ? (
                        <PencilSimple size={14} weight="duotone" className="text-[var(--color-phosphor)] shrink-0" />
                      ) : (
                        <Eye size={14} weight="duotone" className="text-[var(--color-phosphor)] shrink-0" />
                      )}
                      <span className="font-mono text-[12px] truncate">{g.support_actor}</span>
                      <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">{g.scope}</span>
                    </div>
                    <span className="font-mono text-[10px] text-[var(--color-muted)] md:ml-auto">
                      {formatRemaining(g.expires_at, now)} (expires {formatTs(g.expires_at)})
                    </span>
                  </div>
                  <p className="font-mono text-[10px] text-[var(--color-dim)]">
                    {g.reason} <span className="text-[var(--color-muted)]">granted by {g.created_by || "admin"} at {formatTs(g.created_at)}</span>
                  </p>
                  <p className="font-mono text-[9px] uppercase tracking-widest text-[var(--color-dim)]">id {g.id}</p>
                  {confirmId === g.id ? (
                    <div className="space-y-2 border border-[var(--color-phosphor)]/40 p-3">
                      <label className="block space-y-1">
                        <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">revoke reason (optional)</span>
                        <input
                          type="text"
                          value={revokeReason}
                          onChange={(e) => setRevokeReason(e.target.value)}
                          maxLength={500}
                          placeholder="incident closed"
                          className="w-full border border-[var(--color-line)] bg-transparent px-3 py-1.5 font-mono text-[11px] focus:outline-none focus:border-[var(--color-phosphor)]"
                        />
                      </label>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => onRevoke(g.id)}
                          disabled={busyId === g.id}
                          className="inline-flex items-center gap-2 border border-[var(--color-phosphor)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-phosphor)] hover:text-[var(--color-bg)] disabled:opacity-40"
                        >
                          <Prohibit size={12} weight="duotone" />
                          {busyId === g.id ? "revoking..." : "confirm revoke"}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setConfirmId(null);
                            setRevokeReason("");
                          }}
                          className="border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-fg)]"
                        >
                          cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmId(g.id)}
                      className="inline-flex items-center gap-2 border border-[var(--color-line)] px-3 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)]"
                    >
                      <Prohibit size={12} weight="duotone" />
                      revoke
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {historicalGrants.length > 0 ? (
          <section className="panel rounded-[2px] p-4 md:p-6 space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-[11px] uppercase tracking-widest">history</h2>
              <span className="font-mono text-[10px] text-[var(--color-muted)]">{historicalGrants.length} expired or revoked</span>
            </div>
            <ul className="divide-y divide-[var(--color-line)]">
              {historicalGrants.map((g) => (
                <li key={g.id} className="py-2 space-y-1">
                  <div className="flex flex-col md:flex-row md:items-center md:gap-3">
                    <span className="font-mono text-[11px] text-[var(--color-muted)]">{g.support_actor}</span>
                    <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">{g.scope}</span>
                    <span className="font-mono text-[10px] text-[var(--color-dim)] md:ml-auto">
                      {g.revoked_at ? `revoked ${formatTs(g.revoked_at)} by ${g.revoked_by || "admin"}` : `expired ${formatTs(g.expires_at)}`}
                    </span>
                  </div>
                  <p className="font-mono text-[10px] text-[var(--color-dim)]">{g.reason}</p>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </main>
  );
}
