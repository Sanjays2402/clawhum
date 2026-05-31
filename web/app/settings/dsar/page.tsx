"use client";

/**
 * Data Subject Access Request (DSAR) tracker.
 *
 * Privacy ops files an intake when a data subject (typically forwarded
 * from privacy@) asks to access, export, correct, or erase their data.
 * The tracker enforces a statutory due date (default 30 days, tightest
 * applicable GDPR Art 12) and shows overdue items at the top of the
 * queue. Every state change is tenant scoped and audit logged.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Scales,
  Plus,
  Warning,
  ArrowLeft,
  Clock,
  CheckCircle,
  XCircle,
  CaretRight,
  EnvelopeSimple,
  UserCircle,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface EventRow {
  at: number;
  actor: string;
  action: string;
  note: string;
  from_status: string;
  to_status: string;
}

interface RequestRow {
  id: string;
  subject_email: string;
  kind: string;
  status: string;
  note: string;
  created_at: number;
  due_at: number;
  updated_at: number;
  closed_at: number;
  overdue: boolean;
  history: EventRow[];
}

interface ListResp {
  requests: RequestRow[];
  summary: {
    open: number;
    overdue: number;
    total: number;
    by_status: Record<string, number>;
    by_kind: Record<string, number>;
    next_due_at: number | null;
  };
  kinds: string[];
  statuses: string[];
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

function dueLabel(ts: number, overdue: boolean): string {
  if (!ts) return "";
  const diff = ts * 1000 - Date.now();
  const days = Math.round(diff / 86400000);
  if (overdue) return `${Math.abs(days)}d overdue`;
  if (days <= 0) return "due today";
  if (days === 1) return "1d left";
  return `${days}d left`;
}

const STATUS_TONE: Record<string, string> = {
  received: "text-[var(--color-phosphor)]",
  in_progress: "text-amber-400",
  completed: "text-emerald-400",
  rejected: "text-rose-400",
};

export default function DsarPage() {
  useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [filter, setFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState("");
  const [form, setForm] = useState({
    subject_email: "",
    kind: "access",
    note: "",
    due_days: 30,
  });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const url = filter ? `/dsar?status=${encodeURIComponent(filter)}` : "/dsar";
      const r = await fetch(url, { headers: authHeaders() });
      if (!r.ok) {
        const txt = await r.text();
        setState({ kind: "error", status: r.status, message: txt || r.statusText });
        return;
      }
      const data = (await r.json()) as ListResp;
      setState({ kind: "ready", data });
    } catch (e) {
      setState({ kind: "error", status: 0, message: String(e) });
    }
  }, [filter]);

  useEffect(() => { void load(); }, [load]);

  const onCreate = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setCreateErr("");
    const r = await fetch("/dsar", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(form),
    });
    if (!r.ok) {
      setCreateErr((await r.text()) || r.statusText);
      return;
    }
    setForm({ subject_email: "", kind: "access", note: "", due_days: 30 });
    setCreating(false);
    void load();
  };

  const advance = async (id: string, to_status: string) => {
    let note = "";
    if (to_status === "rejected") {
      const entered = window.prompt("Justification for rejection (required, recorded in audit log):");
      if (!entered) return;
      note = entered;
    } else {
      note = window.prompt("Note (optional)") || "";
    }
    const r = await fetch(`/dsar/${id}/advance`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ to_status, note }),
    });
    if (!r.ok) {
      alert((await r.text()) || r.statusText);
      return;
    }
    void load();
  };

  const summary = state.kind === "ready" ? state.data.summary : null;
  const rows = state.kind === "ready" ? state.data.requests : [];
  const kinds = useMemo(
    () => (state.kind === "ready" ? state.data.kinds : ["access", "erasure", "portability", "rectification"]),
    [state],
  );
  const statuses = useMemo(
    () => (state.kind === "ready" ? state.data.statuses : ["received", "in_progress", "completed", "rejected"]),
    [state],
  );

  return (
    <main className="min-h-dvh px-4 py-8 sm:px-8 sm:py-12">
      <div className="mx-auto max-w-5xl space-y-6">
        <header className="flex flex-wrap items-center gap-3">
          <Link href="/settings" className="inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">
            <ArrowLeft size={14} weight="duotone" /> settings
          </Link>
          <h1 className="ml-2 inline-flex items-center gap-2 font-mono text-sm uppercase tracking-widest">
            <Scales size={16} weight="duotone" /> data subject requests
          </h1>
          <button
            type="button"
            onClick={() => setCreating((v) => !v)}
            className="ml-auto inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
          >
            <Plus size={12} weight="duotone" /> file request
          </button>
        </header>

        <p className="font-mono text-[10px] leading-relaxed text-[var(--color-dim)]">
          intake and tracking for GDPR article 15 / 17 / 20 and CCPA section 1798.100 requests. each request carries a statutory due date and every state change is recorded in the audit log with the actor, prior status, new status, and the note you enter. admin role plus MFA required to mutate. terminal requests cannot be reopened, file a new one instead.
        </p>

        {summary && (
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { label: "open", val: summary.open },
              { label: "overdue", val: summary.overdue, tone: summary.overdue > 0 ? "text-rose-400" : undefined },
              { label: "completed", val: summary.by_status.completed ?? 0 },
              { label: "total", val: summary.total },
            ].map((s) => (
              <div key={s.label} className="panel rounded-[2px] p-3">
                <div className="label-xs">{s.label}</div>
                <div className={`mt-1 font-mono text-2xl ${s.tone ?? ""}`}>{s.val}</div>
              </div>
            ))}
          </section>
        )}

        {creating && (
          <form onSubmit={onCreate} className="panel rounded-[2px] p-4 space-y-3">
            <div className="label-xs">new request</div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="label-xs">subject email</span>
                <input
                  type="email"
                  required
                  value={form.subject_email}
                  onChange={(e) => setForm({ ...form, subject_email: e.target.value })}
                  className="mt-1 w-full bg-transparent border border-[var(--color-line)] px-2 py-1 font-mono text-xs"
                />
              </label>
              <label className="block">
                <span className="label-xs">kind</span>
                <select
                  value={form.kind}
                  onChange={(e) => setForm({ ...form, kind: e.target.value })}
                  className="mt-1 w-full bg-transparent border border-[var(--color-line)] px-2 py-1 font-mono text-xs"
                >
                  {kinds.map((k) => (<option key={k} value={k}>{k}</option>))}
                </select>
              </label>
              <label className="block">
                <span className="label-xs">due in days</span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={form.due_days}
                  onChange={(e) => setForm({ ...form, due_days: Number(e.target.value) })}
                  className="mt-1 w-full bg-transparent border border-[var(--color-line)] px-2 py-1 font-mono text-xs"
                />
              </label>
              <label className="block sm:col-span-2">
                <span className="label-xs">intake note (optional)</span>
                <textarea
                  value={form.note}
                  onChange={(e) => setForm({ ...form, note: e.target.value })}
                  rows={2}
                  className="mt-1 w-full bg-transparent border border-[var(--color-line)] px-2 py-1 font-mono text-xs"
                />
              </label>
            </div>
            {createErr && (
              <p className="font-mono text-[10px] text-rose-400">{createErr}</p>
            )}
            <div className="flex gap-2">
              <button type="submit" className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-line)]">
                file
              </button>
              <button type="button" onClick={() => setCreating(false)} className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
                cancel
              </button>
            </div>
          </form>
        )}

        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="label-xs">queue</span>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="ml-auto bg-transparent border border-[var(--color-line)] px-2 py-1 font-mono text-[11px]"
            >
              <option value="">all statuses</option>
              {statuses.map((s) => (<option key={s} value={s}>{s}</option>))}
            </select>
          </div>

          {state.kind === "loading" && (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-12 animate-pulse bg-[var(--color-line)] opacity-30" />
              ))}
            </div>
          )}

          {state.kind === "error" && (
            <div className="flex items-start gap-2 font-mono text-[11px] text-rose-400">
              <Warning size={14} weight="duotone" />
              <div>
                <div>{state.status || "network"} {state.message}</div>
                {state.status === 401 && <div className="text-[var(--color-dim)]">set an admin API key in the header.</div>}
                {state.status === 403 && <div className="text-[var(--color-dim)]">admin role required.</div>}
              </div>
            </div>
          )}

          {state.kind === "ready" && rows.length === 0 && (
            <div className="py-8 text-center font-mono text-[11px] text-[var(--color-dim)]">
              no requests on file. when a data subject contacts you, file the intake here so the clock starts and the audit trail begins.
            </div>
          )}

          {state.kind === "ready" && rows.length > 0 && (
            <ul className="divide-y divide-[var(--color-line)]">
              {rows.map((r) => {
                const terminal = r.status === "completed" || r.status === "rejected";
                const isOpen = expanded === r.id;
                return (
                  <li key={r.id} className="py-2">
                    <button
                      type="button"
                      onClick={() => setExpanded(isOpen ? null : r.id)}
                      className="flex w-full flex-wrap items-center gap-3 text-left"
                    >
                      <CaretRight
                        size={12}
                        weight="duotone"
                        className={`transition-transform ${isOpen ? "rotate-90" : ""}`}
                      />
                      <EnvelopeSimple size={14} weight="duotone" className="text-[var(--color-muted)]" />
                      <span className="font-mono text-xs">{r.subject_email}</span>
                      <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">{r.kind}</span>
                      <span className={`ml-auto font-mono text-[10px] uppercase tracking-widest ${STATUS_TONE[r.status] ?? ""}`}>{r.status}</span>
                      <span className={`font-mono text-[10px] ${r.overdue ? "text-rose-400" : "text-[var(--color-dim)]"}`}>
                        {terminal ? `closed ${formatTs(r.closed_at)}` : dueLabel(r.due_at, r.overdue)}
                      </span>
                    </button>

                    {isOpen && (
                      <div className="ml-6 mt-3 space-y-3 border-l border-[var(--color-line)] pl-4">
                        <div className="grid gap-2 sm:grid-cols-2 font-mono text-[10px] text-[var(--color-dim)]">
                          <div><span className="label-xs mr-1">id</span>{r.id}</div>
                          <div><span className="label-xs mr-1">filed</span>{formatTs(r.created_at)}</div>
                          <div><span className="label-xs mr-1">due</span>{formatTs(r.due_at)}</div>
                          <div><span className="label-xs mr-1">updated</span>{formatTs(r.updated_at)}</div>
                        </div>
                        {r.note && (
                          <div className="font-mono text-[11px] text-[var(--color-muted)] whitespace-pre-wrap">{r.note}</div>
                        )}
                        <ol className="space-y-1">
                          {r.history.map((e, i) => (
                            <li key={i} className="flex items-start gap-2 font-mono text-[10px] text-[var(--color-muted)]">
                              <Clock size={10} weight="duotone" className="mt-1 text-[var(--color-dim)]" />
                              <span className="text-[var(--color-dim)]">{formatTs(e.at)}</span>
                              <UserCircle size={10} weight="duotone" className="mt-1 text-[var(--color-dim)]" />
                              <span>{e.actor || "system"}</span>
                              <span className="text-[var(--color-dim)]">
                                {e.from_status ? `${e.from_status} \u2192 ${e.to_status}` : e.to_status}
                              </span>
                              {e.note && <span className="text-[var(--color-muted)]">/ {e.note}</span>}
                            </li>
                          ))}
                        </ol>
                        {!terminal && (
                          <div className="flex flex-wrap gap-2">
                            {r.status === "received" && (
                              <button onClick={() => advance(r.id, "in_progress")} className="border border-[var(--color-line)] px-3 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]">start</button>
                            )}
                            <button onClick={() => advance(r.id, "completed")} className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1 font-mono text-[10px] uppercase tracking-widest text-emerald-400 hover:bg-[var(--color-line)]">
                              <CheckCircle size={11} weight="duotone" /> complete
                            </button>
                            <button onClick={() => advance(r.id, "rejected")} className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1 font-mono text-[10px] uppercase tracking-widest text-rose-400 hover:bg-[var(--color-line)]">
                              <XCircle size={11} weight="duotone" /> reject
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
