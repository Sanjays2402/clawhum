"use client";

/**
 * Workspace members and invites.
 *
 * Shows the human roster (active members + pending invites), lets a
 * workspace admin invite an email + role, change a member's role,
 * and revoke seats. The freshly minted invite token is shown ONCE so
 * the inviter can paste it into email or Slack out of band.
 *
 * Everything is gated server side: non-admins see the list but mutation
 * endpoints return 403; we render the controls in a disabled-looking
 * state for clarity.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Users,
  UserPlus,
  Copy,
  Check,
  Trash,
  PencilSimple,
  ArrowClockwise,
  Warning,
  ArrowLeft,
  Clock,
  Shield,
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

interface Counts {
  active: number;
  invited: number;
  revoked: number;
}

interface CreatedInvite extends Member {
  invite_token: string;
}

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; members: Member[]; counts: Counts }
  | { kind: "error"; status: number; message: string };

const ROLES = ["reader", "writer", "admin"] as const;

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const k = getApiKey();
  const out: Record<string, string> = extra ? { ...extra } : {};
  if (k) out["X-API-Key"] = k;
  return out;
}

function fmtAgo(ts: number): string {
  if (!ts) return "never";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function fmtExpiry(ts: number): string {
  if (!ts) return "never";
  const d = ts - Date.now() / 1000;
  if (d <= 0) return "expired";
  if (d < 3600) return `in ${Math.floor(d / 60)}m`;
  if (d < 86400) return `in ${Math.floor(d / 3600)}h`;
  return `in ${Math.floor(d / 86400)}d`;
}

export default function MembersPage() {
  useApiKey();
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("reader");
  const [ttlHours, setTtlHours] = useState<string>("168");
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setList({ kind: "loading" });
    try {
      const r = await fetch("/api/members", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        setList({ kind: "error", status: r.status, message: text || r.statusText });
        return;
      }
      const body = (await r.json()) as { members: Member[]; counts: Counts };
      setList({ kind: "ready", members: body.members, counts: body.counts });
    } catch (e: any) {
      setList({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCreating(true);
    setCreated(null);
    try {
      const ttl = ttlHours.trim();
      const body: Record<string, unknown> = { email: email.trim(), role };
      if (ttl !== "" && !Number.isNaN(Number(ttl))) {
        body.ttl_hours = Number(ttl);
      }
      const r = await fetch("/api/members/invite", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        let msg = text || r.statusText;
        try {
          const j = JSON.parse(text);
          if (j?.detail) msg = String(j.detail);
        } catch {}
        setError(`invite failed (${r.status}): ${msg}`);
        return;
      }
      const inv = (await r.json()) as CreatedInvite;
      setCreated(inv);
      setEmail("");
      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setCreating(false);
    }
  }

  async function updateRole(id: string, nextRole: string) {
    setBusyId(id);
    setError(null);
    try {
      const r = await fetch(`/api/members/${id}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ role: nextRole }),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        setError(`update failed (${r.status}): ${text || r.statusText}`);
        return;
      }
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function revoke(id: string, email: string) {
    if (!confirm(`Revoke ${email}? This cannot be undone.`)) return;
    setBusyId(id);
    setError(null);
    try {
      const r = await fetch(`/api/members/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!r.ok && r.status !== 204) {
        const text = await r.text().catch(() => "");
        setError(`revoke failed (${r.status}): ${text || r.statusText}`);
        return;
      }
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function resend(id: string, email: string) {
    setBusyId(id);
    setError(null);
    setCreated(null);
    try {
      const r = await fetch(`/api/members/${id}/resend`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({}),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        let msg = text || r.statusText;
        try {
          const j = JSON.parse(text);
          if (j?.detail) msg = String(j.detail);
        } catch {}
        setError(`resend failed (${r.status}): ${msg}`);
        return;
      }
      const inv = (await r.json()) as CreatedInvite;
      setCreated({ ...inv, email });
      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function copy(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied((c) => (c === label ? null : c)), 1500);
    } catch {}
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-text)]">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6 sm:py-10 space-y-6">
        <div className="flex items-center gap-2">
          <Link
            href="/settings"
            className="inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
          >
            <ArrowLeft size={12} weight="duotone" />
            settings
          </Link>
        </div>

        <header className="space-y-1">
          <div className="flex items-center gap-2">
            <Users size={18} weight="duotone" />
            <h1 className="font-mono text-base uppercase tracking-widest">
              workspace members
            </h1>
          </div>
          <p className="font-mono text-[11px] text-[var(--color-dim)] leading-relaxed">
            invite teammates by email, pick a role, and revoke access when
            they leave. invite tokens are shown exactly once. role changes
            and revokes require admin plus a fresh MFA code.
          </p>
        </header>

        {/* Invite form */}
        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex items-center gap-2">
            <UserPlus size={14} weight="duotone" />
            <span className="label-xs">invite teammate</span>
          </div>
          <form
            onSubmit={invite}
            className="grid grid-cols-1 sm:grid-cols-[1fr_140px_120px_auto] gap-2"
          >
            <input
              type="email"
              required
              placeholder="alex@acme.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] focus:outline-none focus:border-[var(--color-phosphor)]"
            />
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as any)}
              className="bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] uppercase tracking-widest focus:outline-none focus:border-[var(--color-phosphor)]"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              max={8760}
              placeholder="ttl hours"
              value={ttlHours}
              onChange={(e) => setTtlHours(e.target.value)}
              className="bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] focus:outline-none focus:border-[var(--color-phosphor)]"
              title="invite expiry in hours; 0 means never"
            />
            <button
              type="submit"
              disabled={creating || !email.trim()}
              className="border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)] hover:border-[var(--color-phosphor)] disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center justify-center gap-1.5"
            >
              <UserPlus size={12} weight="duotone" />
              {creating ? "inviting..." : "invite"}
            </button>
          </form>
          {error && (
            <div className="border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 font-mono text-[11px] text-[var(--color-warning,#d97706)] inline-flex items-start gap-1.5">
              <Warning size={12} weight="duotone" className="mt-0.5 shrink-0" />
              <span className="break-all">{error}</span>
            </div>
          )}
          {created && (
            <div className="border border-[var(--color-phosphor)] bg-[var(--color-panel)] p-2 space-y-1.5">
              <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)]">
                share this token with {created.email} once
              </div>
              <div className="flex items-center gap-2">
                <code className="flex-1 font-mono text-[11px] break-all bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1">
                  {created.invite_token}
                </code>
                <button
                  type="button"
                  onClick={() => copy("token", created.invite_token)}
                  className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest hover:text-[var(--color-phosphor)] inline-flex items-center gap-1"
                >
                  {copied === "token" ? (
                    <Check size={11} weight="duotone" />
                  ) : (
                    <Copy size={11} weight="duotone" />
                  )}
                  {copied === "token" ? "copied" : "copy"}
                </button>
              </div>
              <div className="font-mono text-[10px] text-[var(--color-dim)]">
                expires {fmtExpiry(created.invite_expires_at)}. they accept
                with: POST /api/members/accept &#123;"token":"..."&#125;
              </div>
            </div>
          )}
        </section>

        {/* Roster */}
        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Shield size={14} weight="duotone" />
            <span className="label-xs">roster</span>
            {list.kind === "ready" && (
              <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                {list.counts.active} active &middot; {list.counts.invited} pending
              </span>
            )}
          </div>

          {list.kind === "loading" && (
            <div className="space-y-1.5">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-9 border border-[var(--color-line)] bg-[var(--color-panel)] animate-pulse"
                />
              ))}
            </div>
          )}

          {list.kind === "error" && (
            <div className="border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-2 font-mono text-[11px] inline-flex items-start gap-1.5">
              <Warning size={12} weight="duotone" className="mt-0.5 shrink-0" />
              <span>
                could not load roster ({list.status}): {list.message}
              </span>
            </div>
          )}

          {list.kind === "ready" && list.members.length === 0 && (
            <div className="border border-dashed border-[var(--color-line)] px-3 py-6 text-center font-mono text-[11px] text-[var(--color-dim)]">
              no members yet. invite your first teammate above.
            </div>
          )}

          {list.kind === "ready" && list.members.length > 0 && (
            <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)]">
              {list.members.map((m) => (
                <li
                  key={m.id}
                  className="flex flex-col sm:flex-row sm:items-center gap-2 px-3 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-mono text-[12px] truncate">
                      {m.email}
                    </div>
                    <div className="font-mono text-[10px] text-[var(--color-dim)] flex flex-wrap items-center gap-x-3 gap-y-0.5">
                      <span className="uppercase tracking-widest">
                        {m.status}
                      </span>
                      <span>invited {fmtAgo(m.invited_at)}</span>
                      {m.status === "active" && m.accepted_at > 0 && (
                        <span>joined {fmtAgo(m.accepted_at)}</span>
                      )}
                      {m.status === "invited" && (
                        <span className="inline-flex items-center gap-1">
                          <Clock size={9} weight="duotone" />
                          {fmtExpiry(m.invite_expires_at)}
                        </span>
                      )}
                      <span>by {m.invited_by}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {m.status === "invited" && (
                      <button
                        type="button"
                        onClick={() => resend(m.id, m.email)}
                        disabled={busyId === m.id}
                        aria-label={`resend invite for ${m.email}`}
                        title="rotate invite token and extend expiry"
                        className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)] disabled:opacity-40 inline-flex items-center gap-1"
                      >
                        <ArrowClockwise size={11} weight="duotone" />
                        resend
                      </button>
                    )}
                    <select
                      value={m.role}
                      disabled={busyId === m.id}
                      onChange={(e) => updateRole(m.id, e.target.value)}
                      aria-label={`role for ${m.email}`}
                      className="bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1 font-mono text-[11px] uppercase tracking-widest focus:outline-none focus:border-[var(--color-phosphor)]"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => revoke(m.id, m.email)}
                      disabled={busyId === m.id}
                      aria-label={`revoke ${m.email}`}
                      className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-warning,#d97706)] hover:border-[var(--color-warning,#d97706)] disabled:opacity-40 inline-flex items-center gap-1"
                    >
                      <Trash size={11} weight="duotone" />
                      revoke
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
