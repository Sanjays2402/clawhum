"use client";

/**
 * Sub-processor registry, acknowledgement, and change notification
 * subscriptions for the workspace. Maps to GDPR Art. 28(2) evidence
 * every enterprise procurement review asks for.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Buildings,
  Plus,
  Trash,
  Warning,
  ArrowLeft,
  CheckCircle,
  Clock,
  Bell,
  Globe,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface Processor {
  id: string;
  name: string;
  purpose: string;
  region: string;
  data_categories: string[];
  dpa_url: string;
  status: string;
  created_at: number;
  updated_at: number;
}

interface RegistryResp {
  revision: number;
  processors: Processor[];
  statuses: string[];
  can_manage: boolean;
}

interface AckResp {
  tenant_id: string;
  revision: number;
  acknowledged_by: string;
  acknowledged_at: number;
  current_revision: number;
  up_to_date: boolean;
}

interface Subscription {
  id: string;
  email: string;
  created_at: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; reg: RegistryResp; ack: AckResp; subs: Subscription[] }
  | { kind: "error"; status: number; message: string };

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k, ...extra } : { ...extra };
}

function jsonHeaders(): Record<string, string> {
  return authHeaders({ "Content-Type": "application/json" });
}

function formatTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function statusTone(status: string): string {
  if (status === "active") return "bg-emerald-500/10 text-emerald-300 border-emerald-500/30";
  if (status === "proposed") return "bg-amber-500/10 text-amber-300 border-amber-500/30";
  return "bg-zinc-500/10 text-zinc-400 border-zinc-500/30";
}

export default function SubprocessorsPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [includeRemoved, setIncludeRemoved] = useState(false);

  const [newEmail, setNewEmail] = useState("");
  const [subBusy, setSubBusy] = useState(false);
  const [subError, setSubError] = useState<string | null>(null);

  const [ackBusy, setAckBusy] = useState(false);
  const [ackError, setAckError] = useState<string | null>(null);

  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    purpose: "",
    region: "",
    data_categories: "",
    dpa_url: "",
    status: "active",
  });
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(
    async (showLoading = true) => {
      if (showLoading) setState({ kind: "loading" });
      try {
        const qs = includeRemoved ? "?include_removed=true" : "";
        const [rReg, rAck, rSubs] = await Promise.all([
          fetch(`/api/subprocessors${qs}`, { headers: authHeaders() }),
          fetch("/api/subprocessors/acknowledgement", { headers: authHeaders() }),
          fetch("/api/subprocessors/subscriptions", { headers: authHeaders() }),
        ]);
        if (!rReg.ok) {
          setState({ kind: "error", status: rReg.status, message: await rReg.text() });
          return;
        }
        if (!rAck.ok) {
          setState({ kind: "error", status: rAck.status, message: await rAck.text() });
          return;
        }
        if (!rSubs.ok) {
          setState({ kind: "error", status: rSubs.status, message: await rSubs.text() });
          return;
        }
        const reg = (await rReg.json()) as RegistryResp;
        const ack = (await rAck.json()) as AckResp;
        const subs = ((await rSubs.json()) as { subscriptions: Subscription[] }).subscriptions;
        setState({ kind: "ready", reg, ack, subs });
      } catch (e) {
        setState({
          kind: "error",
          status: 0,
          message: e instanceof Error ? e.message : "network error",
        });
      }
    },
    [includeRemoved],
  );

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function acknowledge() {
    if (state.kind !== "ready") return;
    setAckBusy(true);
    setAckError(null);
    try {
      const r = await fetch("/api/subprocessors/acknowledgement", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ revision: state.reg.revision }),
      });
      if (!r.ok) {
        setAckError(await r.text());
      } else {
        await refresh(false);
      }
    } catch (e) {
      setAckError(e instanceof Error ? e.message : "failed");
    } finally {
      setAckBusy(false);
    }
  }

  async function addSubscription(e: React.FormEvent) {
    e.preventDefault();
    if (!newEmail.trim()) return;
    setSubBusy(true);
    setSubError(null);
    try {
      const r = await fetch("/api/subprocessors/subscriptions", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ email: newEmail.trim() }),
      });
      if (!r.ok) {
        const body = await r.text();
        try {
          setSubError(JSON.parse(body)?.detail || body);
        } catch {
          setSubError(body);
        }
      } else {
        setNewEmail("");
        await refresh(false);
      }
    } catch (err) {
      setSubError(err instanceof Error ? err.message : "failed");
    } finally {
      setSubBusy(false);
    }
  }

  async function removeSubscription(id: string) {
    setBusyId(id);
    try {
      const r = await fetch(`/api/subprocessors/subscriptions/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (r.ok) await refresh(false);
    } finally {
      setBusyId(null);
    }
  }

  async function createProcessor(e: React.FormEvent) {
    e.preventDefault();
    setFormBusy(true);
    setFormError(null);
    try {
      const cats = form.data_categories
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean);
      const r = await fetch("/api/subprocessors", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ ...form, data_categories: cats }),
      });
      if (!r.ok) {
        const body = await r.text();
        try {
          setFormError(JSON.parse(body)?.detail || body);
        } catch {
          setFormError(body);
        }
      } else {
        setForm({
          name: "",
          purpose: "",
          region: "",
          data_categories: "",
          dpa_url: "",
          status: "active",
        });
        setAddOpen(false);
        await refresh(false);
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "failed");
    } finally {
      setFormBusy(false);
    }
  }

  async function removeProcessor(id: string) {
    setBusyId(id);
    try {
      const r = await fetch(`/api/subprocessors/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (r.ok) await refresh(false);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-10">
      <Link
        href="/settings"
        className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-100"
      >
        <ArrowLeft size={16} weight="duotone" /> Back to settings
      </Link>

      <header className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-zinc-100">
            <Buildings size={26} weight="duotone" /> Sub-processors
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">
            The third parties that may process your data on our behalf
            (GDPR Art. 28). Acknowledge the current revision to keep
            your evidence trail clean, and subscribe the right email
            addresses to be notified before the list changes.
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={includeRemoved}
            onChange={(e) => setIncludeRemoved(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-zinc-700 bg-zinc-900"
          />
          Show removed
        </label>
      </header>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-3">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-20 animate-pulse rounded-lg border border-zinc-800 bg-zinc-900/40"
            />
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-3 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-300">
          <Warning size={20} weight="duotone" />
          <div>
            <div className="font-medium">
              Could not load sub-processors ({state.status || "network"})
            </div>
            <div className="mt-1 break-words text-xs text-red-300/80">
              {state.message || "unknown error"}
            </div>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <section className="mt-6 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                {state.ack.up_to_date ? (
                  <CheckCircle size={22} weight="duotone" className="text-emerald-400" />
                ) : (
                  <Clock size={22} weight="duotone" className="text-amber-400" />
                )}
                <div>
                  <div className="text-sm font-medium text-zinc-100">
                    {state.ack.up_to_date
                      ? "Acknowledgement up to date"
                      : "Acknowledgement out of date"}
                  </div>
                  <div className="text-xs text-zinc-400">
                    Current revision r{state.ack.current_revision}
                    {state.ack.revision > 0
                      ? `, you acknowledged r${state.ack.revision} on ${formatTs(state.ack.acknowledged_at)}${state.ack.acknowledged_by ? ` by ${state.ack.acknowledged_by}` : ""}`
                      : ", never acknowledged"}
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={acknowledge}
                disabled={ackBusy || state.ack.up_to_date || state.reg.revision === 0}
                className="inline-flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <CheckCircle size={16} weight="duotone" />
                {ackBusy ? "Saving" : `Acknowledge r${state.reg.revision}`}
              </button>
            </div>
            {ackError && (
              <div className="mt-3 break-words rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
                {ackError}
              </div>
            )}
          </section>

          <section className="mt-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">
                Registry
              </h2>
              {state.reg.can_manage && (
                <button
                  type="button"
                  onClick={() => setAddOpen((v) => !v)}
                  className="inline-flex items-center gap-2 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
                >
                  <Plus size={14} weight="duotone" />
                  {addOpen ? "Close" : "Add sub-processor"}
                </button>
              )}
            </div>

            {addOpen && state.reg.can_manage && (
              <form
                onSubmit={createProcessor}
                className="mt-3 grid gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 sm:grid-cols-2"
              >
                <label className="text-xs text-zinc-400 sm:col-span-1">
                  Name
                  <input
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                  />
                </label>
                <label className="text-xs text-zinc-400 sm:col-span-1">
                  Region
                  <input
                    value={form.region}
                    onChange={(e) => setForm({ ...form, region: e.target.value })}
                    placeholder="US, EU"
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                  />
                </label>
                <label className="text-xs text-zinc-400 sm:col-span-2">
                  Purpose
                  <input
                    value={form.purpose}
                    onChange={(e) => setForm({ ...form, purpose: e.target.value })}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                  />
                </label>
                <label className="text-xs text-zinc-400 sm:col-span-2">
                  Data categories (comma separated)
                  <input
                    value={form.data_categories}
                    onChange={(e) => setForm({ ...form, data_categories: e.target.value })}
                    placeholder="email, billing address"
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                  />
                </label>
                <label className="text-xs text-zinc-400 sm:col-span-1">
                  DPA URL
                  <input
                    type="url"
                    value={form.dpa_url}
                    onChange={(e) => setForm({ ...form, dpa_url: e.target.value })}
                    placeholder="https://"
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                  />
                </label>
                <label className="text-xs text-zinc-400 sm:col-span-1">
                  Status
                  <select
                    value={form.status}
                    onChange={(e) => setForm({ ...form, status: e.target.value })}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100"
                  >
                    {state.reg.statuses.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
                {formError && (
                  <div className="break-words rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300 sm:col-span-2">
                    {formError}
                  </div>
                )}
                <div className="sm:col-span-2">
                  <button
                    type="submit"
                    disabled={formBusy || !form.name.trim()}
                    className="inline-flex items-center gap-2 rounded-md border border-zinc-700 bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
                  >
                    {formBusy ? "Saving" : "Save sub-processor"}
                  </button>
                </div>
              </form>
            )}

            {state.reg.processors.length === 0 ? (
              <div className="mt-3 rounded-lg border border-dashed border-zinc-800 bg-zinc-900/20 p-8 text-center text-sm text-zinc-500">
                The sub-processor registry is empty. Once operators seed
                the list, every workspace will be asked to acknowledge it.
              </div>
            ) : (
              <ul className="mt-3 divide-y divide-zinc-800 overflow-hidden rounded-lg border border-zinc-800">
                {state.reg.processors.map((p) => (
                  <li
                    key={p.id}
                    className="flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:justify-between"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-zinc-100">{p.name}</span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${statusTone(p.status)}`}
                        >
                          {p.status}
                        </span>
                        {p.region && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[10px] text-zinc-300">
                            <Globe size={10} weight="duotone" />
                            {p.region}
                          </span>
                        )}
                      </div>
                      {p.purpose && (
                        <div className="mt-1 text-sm text-zinc-300">{p.purpose}</div>
                      )}
                      {p.data_categories.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {p.data_categories.map((c) => (
                            <span
                              key={c}
                              className="rounded bg-zinc-800/60 px-1.5 py-0.5 text-[10px] text-zinc-300"
                            >
                              {c}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="mt-1 text-[11px] text-zinc-500">
                        Updated {formatTs(p.updated_at)}
                        {p.dpa_url && (
                          <>
                            {" · "}
                            <a
                              href={p.dpa_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-zinc-300 underline hover:text-zinc-100"
                            >
                              DPA
                            </a>
                          </>
                        )}
                      </div>
                    </div>
                    {state.reg.can_manage && (
                      <button
                        type="button"
                        onClick={() => removeProcessor(p.id)}
                        disabled={busyId === p.id}
                        className="inline-flex items-center gap-1 self-start rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs text-red-300 hover:bg-red-500/20 disabled:opacity-50"
                      >
                        <Trash size={12} weight="duotone" />
                        Remove
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mt-8">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
              <Bell size={14} weight="duotone" /> Change notifications
            </h2>
            <p className="mt-1 text-xs text-zinc-500">
              Email addresses we notify before the registry changes.
              Add your DPO, security inbox, and the people on your
              vendor management team.
            </p>

            <form onSubmit={addSubscription} className="mt-3 flex flex-wrap gap-2">
              <input
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="dpo@yourcompany.com"
                className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100"
              />
              <button
                type="submit"
                disabled={subBusy || !newEmail.trim()}
                className="inline-flex items-center gap-2 rounded-md border border-zinc-700 bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
              >
                <Plus size={14} weight="duotone" /> Subscribe
              </button>
            </form>
            {subError && (
              <div className="mt-2 break-words rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">
                {subError}
              </div>
            )}

            {state.subs.length === 0 ? (
              <div className="mt-3 rounded-lg border border-dashed border-zinc-800 bg-zinc-900/20 p-6 text-center text-xs text-zinc-500">
                No subscribers yet. Without one, the only way to learn
                about a change is to revisit this page.
              </div>
            ) : (
              <ul className="mt-3 divide-y divide-zinc-800 overflow-hidden rounded-lg border border-zinc-800">
                {state.subs.map((s) => (
                  <li
                    key={s.id}
                    className="flex items-center justify-between gap-3 p-3 text-sm"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-zinc-100">{s.email}</div>
                      <div className="text-[11px] text-zinc-500">
                        Added {formatTs(s.created_at)}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeSubscription(s.id)}
                      disabled={busyId === s.id}
                      className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                    >
                      <Trash size={12} weight="duotone" /> Remove
                    </button>
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
