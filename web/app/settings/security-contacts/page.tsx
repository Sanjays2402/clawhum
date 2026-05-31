"use client";

/**
 * Workspace security and breach notification contacts.
 *
 * Admins register the people we will reach during a security incident
 * or a personal data breach (GDPR Art 33, SOC2 CC7.4). One contact
 * per workspace may be marked primary; the primary is the first
 * person paged. The roster is tenant scoped and every mutation is
 * audit logged through the standard middleware.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldWarning,
  Plus,
  Trash,
  Warning,
  ArrowLeft,
  Star,
  EnvelopeSimple,
  Phone,
  UserCircle,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface ContactRow {
  id: string;
  email: string;
  name: string;
  role: string;
  phone: string;
  primary: boolean;
  created_at: number;
}

interface ListResp {
  contacts: ContactRow[];
  primary_id: string | null;
  roles: string[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ListResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

const DEFAULT_ROLES = ["security", "privacy", "legal", "ops"];

export default function SecurityContactsPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("security");
  const [phone, setPhone] = useState("");
  const [primary, setPrimary] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/security-contacts", { headers: authHeaders() });
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
    if (!email.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/security-contacts", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          email: email.trim(),
          name: name.trim(),
          role: role.trim(),
          phone: phone.trim(),
          primary,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setCreateError(body.detail || `Request failed (${r.status})`);
        return;
      }
      setEmail("");
      setName("");
      setPhone("");
      setPrimary(false);
      setRole("security");
      await refresh();
    } finally {
      setCreating(false);
    }
  }

  async function onDelete(id: string) {
    setBusyId(id);
    try {
      const r = await fetch(
        `/api/security-contacts/${encodeURIComponent(id)}`,
        { method: "DELETE", headers: authHeaders() },
      );
      if (!r.ok && r.status !== 204) return;
      setConfirmId(null);
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function onPromote(id: string) {
    setBusyId(id);
    try {
      const r = await fetch(
        `/api/security-contacts/${encodeURIComponent(id)}/primary`,
        { method: "POST", headers: authHeaders() },
      );
      if (!r.ok) return;
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  const roles =
    state.kind === "ready" && state.data.roles.length > 0
      ? state.data.roles
      : DEFAULT_ROLES;

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 text-zinc-100">
      <div className="mb-6 flex items-center gap-3 text-sm text-zinc-400">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 hover:text-zinc-200"
        >
          <ArrowLeft size={16} weight="duotone" /> settings
        </Link>
        <span>/</span>
        <span className="text-zinc-200">security contacts</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <ShieldWarning size={26} weight="duotone" className="text-amber-300" />
          Security contacts
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          The people we will reach during a security incident or a personal
          data breach. The primary contact is paged first. Required for
          GDPR Article 33 notifications and SOC2 incident communication.
        </p>
      </header>

      <section
        aria-labelledby="add-contact"
        className="mb-8 rounded-xl border border-zinc-800 bg-zinc-950/60 p-5"
      >
        <h2
          id="add-contact"
          className="mb-4 flex items-center gap-2 text-sm font-medium text-zinc-200"
        >
          <Plus size={16} weight="duotone" /> Add a contact
        </h2>
        <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            <span>Email</span>
            <input
              type="email"
              required
              placeholder="soc@acme.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-amber-500 focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            <span>Name</span>
            <input
              type="text"
              placeholder="SOC Team"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-amber-500 focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            <span>Role</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-amber-500 focus:outline-none"
            >
              {roles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            <span>Phone (optional)</span>
            <input
              type="tel"
              placeholder="+1 555 0100"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              maxLength={64}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-amber-500 focus:outline-none"
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-zinc-300 sm:col-span-2">
            <input
              type="checkbox"
              checked={primary}
              onChange={(e) => setPrimary(e.target.checked)}
              className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 text-amber-500 focus:ring-amber-500"
            />
            Mark as primary contact (the previous primary will be demoted)
          </label>
          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={creating || !email.trim()}
              className="inline-flex items-center justify-center gap-1 rounded-md bg-amber-500 px-4 py-2 text-sm font-medium text-zinc-950 shadow-sm transition hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Plus size={16} weight="duotone" />
              {creating ? "adding" : "add contact"}
            </button>
          </div>
        </form>
        {createError && (
          <p className="mt-3 flex items-center gap-1 text-xs text-rose-400">
            <Warning size={14} weight="duotone" /> {createError}
          </p>
        )}
      </section>

      <section
        aria-labelledby="roster"
        className="rounded-xl border border-zinc-800 bg-zinc-950/60"
      >
        <header className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <h2
            id="roster"
            className="flex items-center gap-2 text-sm font-medium text-zinc-200"
          >
            <UserCircle size={16} weight="duotone" /> Roster
          </h2>
          {state.kind === "ready" && (
            <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
              {state.data.contacts.length} on file
            </span>
          )}
        </header>

        {state.kind === "loading" && (
          <ul className="divide-y divide-zinc-900">
            {[0, 1, 2].map((i) => (
              <li
                key={i}
                className="flex items-center justify-between px-5 py-4"
              >
                <div className="h-4 w-2/3 animate-pulse rounded bg-zinc-800" />
                <div className="h-4 w-20 animate-pulse rounded bg-zinc-800" />
              </li>
            ))}
          </ul>
        )}

        {state.kind === "error" && (
          <div className="flex items-start gap-3 px-5 py-6 text-sm text-rose-300">
            <Warning size={18} weight="duotone" />
            <div>
              <div className="font-medium">Could not load contacts</div>
              <div className="mt-1 text-xs text-rose-200/70">
                {state.status ? `HTTP ${state.status} ` : ""}
                {state.message}
              </div>
            </div>
          </div>
        )}

        {state.kind === "ready" && state.data.contacts.length === 0 && (
          <div className="px-5 py-10 text-center text-sm text-zinc-400">
            <ShieldWarning
              size={28}
              weight="duotone"
              className="mx-auto mb-2 text-zinc-600"
            />
            No security contacts yet. Add at least one so we can reach
            you during an incident.
          </div>
        )}

        {state.kind === "ready" && state.data.contacts.length > 0 && (
          <ul className="divide-y divide-zinc-900">
            {state.data.contacts.map((c) => (
              <li
                key={c.id}
                className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    {c.primary && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] uppercase tracking-wide text-amber-300">
                        <Star size={11} weight="duotone" /> primary
                      </span>
                    )}
                    <span className="font-medium text-zinc-100">
                      {c.name || c.email}
                    </span>
                    <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400">
                      {c.role}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-zinc-500">
                    <span className="inline-flex items-center gap-1">
                      <EnvelopeSimple size={12} weight="duotone" />
                      <code className="text-zinc-400">{c.email}</code>
                    </span>
                    {c.phone && (
                      <span className="inline-flex items-center gap-1">
                        <Phone size={12} weight="duotone" />
                        <code className="text-zinc-400">{c.phone}</code>
                      </span>
                    )}
                    <span>added {formatTs(c.created_at)}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {!c.primary && (
                    <button
                      type="button"
                      onClick={() => onPromote(c.id)}
                      disabled={busyId === c.id}
                      className="inline-flex items-center gap-1 rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:border-amber-500/60 hover:text-amber-300 disabled:opacity-50"
                    >
                      <Star size={13} weight="duotone" /> make primary
                    </button>
                  )}
                  {confirmId === c.id ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setConfirmId(null)}
                        className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900"
                      >
                        cancel
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelete(c.id)}
                        disabled={busyId === c.id}
                        className="inline-flex items-center gap-1 rounded-md bg-rose-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-400 disabled:opacity-50"
                      >
                        <Trash size={13} weight="duotone" />
                        {busyId === c.id ? "removing" : "confirm remove"}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmId(c.id)}
                      className="inline-flex items-center gap-1 rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:border-rose-500/60 hover:text-rose-300"
                    >
                      <Trash size={13} weight="duotone" /> remove
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
