"use client";

/**
 * Workspace security incident console.
 *
 * What an enterprise security reviewer expects to see here:
 *
 *   - The open queue with a 72 hour regulator-notify clock per
 *     incident (GDPR Article 33).
 *   - One-click declare with severity, title, and discovery time.
 *   - Drill-in to advance status, append timeline notes, and record
 *     regulator and data subject notification (Article 34, SOC2
 *     CC7.3).
 *
 * Reads are strictly scoped to the current workspace on the backend.
 * Mutations are admin only and MFA gated; the audit middleware records
 * every change.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Siren,
  Plus,
  Warning,
  ShieldCheck,
  Clock,
  ArrowRight,
  CheckCircle,
  ChatText,
  Megaphone,
  UsersThree,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface IncidentEvent {
  at: number;
  actor: string;
  kind: string;
  note: string;
  from_status: string;
  to_status: string;
}

interface Incident {
  id: string;
  title: string;
  severity: string;
  status: string;
  detail: string;
  discovered_at: number;
  created_at: number;
  updated_at: number;
  closed_at: number;
  regulator_notified_at: number;
  regulator_name: string;
  regulator_reference: string;
  subjects_notified_at: number;
  affected_count: number;
  notify_deadline_at: number;
  notify_overdue: boolean;
  history: IncidentEvent[];
}

interface ListResponse {
  incidents: Incident[];
  summary: { total: number; open: number; overdue: number };
  severities: string[];
  statuses: string[];
  notify_deadline_seconds: number;
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

function fmtCountdown(deadline: number): string {
  if (!deadline) return "";
  const delta = deadline - Date.now() / 1000;
  const sign = delta < 0 ? "-" : "";
  const abs = Math.abs(delta);
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  return `${sign}${h}h ${m.toString().padStart(2, "0")}m`;
}

const SEV_TONE: Record<string, string> = {
  low: "text-sky-300 border-sky-500/30 bg-sky-500/5",
  medium: "text-amber-300 border-amber-500/30 bg-amber-500/5",
  high: "text-orange-300 border-orange-500/30 bg-orange-500/5",
  critical: "text-rose-300 border-rose-500/30 bg-rose-500/5",
};

const STATUS_TONE: Record<string, string> = {
  open: "text-rose-300 border-rose-500/30 bg-rose-500/5",
  contained: "text-amber-300 border-amber-500/30 bg-amber-500/5",
  resolved: "text-emerald-300 border-emerald-500/30 bg-emerald-500/5",
  closed_no_action: "text-[var(--color-dim)] border-[var(--color-border)] bg-transparent",
};

export default function IncidentsPage() {
  useApiKey();
  const [data, setData] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = filter ? `?status=${encodeURIComponent(filter)}` : "";
      const r = await fetch(`/api/incidents${qs}`, { headers: authHeaders(), cache: "no-store" });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      setData((await r.json()) as ListResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { refresh(); }, [refresh]);

  const selected = useMemo(
    () => data?.incidents.find((i) => i.id === selectedId) ?? null,
    [data, selectedId],
  );

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-fg)]">
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">
        <Link href="/admin" className="inline-flex items-center gap-1 text-xs text-[var(--color-dim)] hover:text-[var(--color-fg)]">
          <ArrowLeft size={12} weight="duotone" />
          back to admin
        </Link>

        <header className="mt-4 mb-8">
          <div className="flex items-center gap-2">
            <Siren size={18} weight="duotone" />
            <h1 className="text-lg font-medium">security incidents</h1>
          </div>
          <p className="mt-2 text-sm text-[var(--color-dim)]">
            System of record for personal data breaches. Declare an
            incident as soon as it is discovered; the 72 hour clock for
            GDPR Article 33 regulator notification starts at discovery
            and is shown next to every open entry. Mutations require the
            admin role and a fresh MFA code, and are written to the
            audit chain.
          </p>
        </header>

        {data ? (
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="open" value={data.summary.open} />
            <Stat label="overdue" value={data.summary.overdue} tone={data.summary.overdue > 0 ? "danger" : undefined} />
            <Stat label="total" value={data.summary.total} />
            <Stat label="deadline" value={`${Math.round(data.notify_deadline_seconds / 3600)}h`} />
          </div>
        ) : null}

        <DeclareForm
          severities={data?.severities ?? ["low", "medium", "high", "critical"]}
          onDeclared={() => refresh()}
        />

        <div className="mt-8 flex flex-wrap items-center gap-2">
          <FilterBtn current={filter} value="" set={setFilter}>all</FilterBtn>
          {(data?.statuses ?? []).map((s) => (
            <FilterBtn key={s} current={filter} value={s} set={setFilter}>
              {s.replaceAll("_", " ")}
            </FilterBtn>
          ))}
        </div>

        {loading && !data ? (
          <div className="mt-6 rounded-md border border-[var(--color-border)] p-6">
            <div className="h-3 w-32 animate-pulse rounded bg-[var(--color-border)]" />
            <div className="mt-3 h-3 w-48 animate-pulse rounded bg-[var(--color-border)]" />
          </div>
        ) : error ? (
          <div className="mt-6 rounded-md border border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-300">
            <div className="flex items-center gap-2">
              <Warning size={14} weight="duotone" />
              <span>Could not load incidents.</span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap text-xs opacity-80">{error}</pre>
          </div>
        ) : data && data.incidents.length === 0 ? (
          <div className="mt-6 rounded-md border border-dashed border-[var(--color-border)] p-8 text-center text-sm text-[var(--color-dim)]">
            <ShieldCheck size={20} weight="duotone" className="mx-auto mb-2" />
            No incidents match this filter. A clean queue is the goal.
          </div>
        ) : data ? (
          <ul className="mt-6 space-y-3">
            {data.incidents.map((inc) => (
              <li key={inc.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(selectedId === inc.id ? null : inc.id)}
                  className="w-full rounded-md border border-[var(--color-border)] p-4 text-left hover:border-[var(--color-fg)]/30"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${SEV_TONE[inc.severity] ?? ""}`}>{inc.severity}</span>
                        <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${STATUS_TONE[inc.status] ?? ""}`}>{inc.status.replaceAll("_", " ")}</span>
                        <span className="font-mono text-[10px] text-[var(--color-dim)]">{inc.id}</span>
                      </div>
                      <div className="mt-2 text-sm">{inc.title}</div>
                      <div className="mt-1 text-xs text-[var(--color-dim)]">discovered {fmtDate(inc.discovered_at)}</div>
                    </div>
                    {inc.status === "open" || inc.status === "contained" ? (
                      <div className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs ${inc.notify_overdue ? "border-rose-500/40 bg-rose-500/10 text-rose-200" : "border-[var(--color-border)] text-[var(--color-dim)]"}`}>
                        <Clock size={12} weight="duotone" />
                        {inc.regulator_notified_at
                          ? "regulator notified"
                          : inc.notify_overdue
                          ? `overdue by ${fmtCountdown(inc.notify_deadline_at).replace("-", "")}`
                          : `notify in ${fmtCountdown(inc.notify_deadline_at)}`}
                      </div>
                    ) : null}
                  </div>
                </button>
                {selected?.id === inc.id ? (
                  <Detail inc={inc} statuses={data.statuses} onChanged={() => refresh()} />
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: "danger" }) {
  return (
    <div className={`rounded-md border p-3 ${tone === "danger" ? "border-rose-500/30 bg-rose-500/5" : "border-[var(--color-border)]"}`}>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-dim)]">{label}</div>
      <div className={`mt-1 font-mono text-lg ${tone === "danger" ? "text-rose-300" : ""}`}>{value}</div>
    </div>
  );
}

function FilterBtn({ current, value, set, children }: { current: string; value: string; set: (v: string) => void; children: React.ReactNode }) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => set(value)}
      className={`rounded border px-2 py-1 text-xs ${active ? "border-[var(--color-fg)]/40 bg-[var(--color-fg)]/5" : "border-[var(--color-border)] text-[var(--color-dim)] hover:text-[var(--color-fg)]"}`}
    >
      {children}
    </button>
  );
}

function DeclareForm({ severities, onDeclared }: { severities: string[]; onDeclared: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState("medium");
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/incidents", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ title, severity, detail }),
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      setTitle(""); setDetail(""); setSeverity("medium"); setOpen(false);
      onDeclared();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [title, severity, detail, onDeclared]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded border border-[var(--color-border)] px-3 py-1.5 text-sm hover:border-[var(--color-fg)]/30"
      >
        <Plus size={14} weight="duotone" />
        declare incident
      </button>
    );
  }

  return (
    <div className="rounded-md border border-[var(--color-border)] p-4">
      <div className="text-xs uppercase tracking-wide text-[var(--color-dim)]">declare incident</div>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-[1fr_160px]">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="short title (required)"
          maxLength={200}
          className="rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
        />
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          className="rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
        >
          {severities.map((s) => (<option key={s} value={s}>{s}</option>))}
        </select>
      </div>
      <textarea
        value={detail}
        onChange={(e) => setDetail(e.target.value)}
        placeholder="detail (optional, max 8000 chars)"
        maxLength={8000}
        rows={4}
        className="mt-3 w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
      />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || title.trim().length === 0}
          className="inline-flex items-center gap-2 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <CheckCircle size={14} weight="duotone" />
          {busy ? "declaring" : "declare"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setErr(null); }}
          className="rounded border border-[var(--color-border)] px-3 py-1.5 text-sm hover:bg-[var(--color-border)]/30"
        >
          cancel
        </button>
        <span className="text-xs text-[var(--color-dim)]">requires admin role and a fresh MFA code</span>
      </div>
      {err ? (<div className="mt-3 rounded border border-rose-500/30 bg-rose-500/5 p-2 text-xs text-rose-300">{err}</div>) : null}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-24 shrink-0 text-[var(--color-dim)]">{k}</span>
      <span className="font-mono break-all">{v}</span>
    </div>
  );
}

function TabBtn({ active, set, icon, children }: { active: boolean; set: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={set}
      className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs ${active ? "border-[var(--color-fg)]/40 bg-[var(--color-fg)]/5" : "border-[var(--color-border)] text-[var(--color-dim)] hover:text-[var(--color-fg)]"}`}
    >
      {icon}
      {children}
    </button>
  );
}

function ActionBtn({ disabled, onClick, label }: { disabled: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-2 rounded border border-[var(--color-fg)]/30 bg-[var(--color-fg)]/5 px-3 py-1.5 text-sm hover:bg-[var(--color-fg)]/10 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {label}
    </button>
  );
}

function Detail({ inc, statuses, onChanged }: { inc: Incident; statuses: string[]; onChanged: () => void }) {
  const [tab, setTab] = useState<"note" | "advance" | "regulator" | "subjects">("note");
  const [note, setNote] = useState("");
  const [toStatus, setToStatus] = useState<string>(() => statuses.find((s) => s !== inc.status) ?? "contained");
  const [regName, setRegName] = useState("");
  const [regRef, setRegRef] = useState("");
  const [affected, setAffected] = useState<string>("0");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const post = useCallback(async (suffix: string, body: object) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/incidents/${inc.id}${suffix}`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      setNote("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [inc.id, onChanged]);

  const terminal = inc.status === "resolved" || inc.status === "closed_no_action";

  return (
    <div className="mt-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/40 p-4">
      {inc.detail ? (
        <pre className="mb-4 whitespace-pre-wrap rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-xs text-[var(--color-dim)]">{inc.detail}</pre>
      ) : null}

      <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
        <Row k="created" v={fmtDate(inc.created_at)} />
        <Row k="updated" v={fmtDate(inc.updated_at)} />
        <Row k="closed" v={fmtDate(inc.closed_at)} />
        <Row k="regulator" v={inc.regulator_notified_at ? `${inc.regulator_name} (${inc.regulator_reference || "no ref"}) at ${fmtDate(inc.regulator_notified_at)}` : "not notified"} />
        <Row k="subjects" v={inc.subjects_notified_at ? `${inc.affected_count} affected, ${fmtDate(inc.subjects_notified_at)}` : "not notified"} />
      </dl>

      {!terminal ? (
        <div className="mt-4">
          <div className="flex flex-wrap gap-2">
            <TabBtn active={tab === "note"} set={() => setTab("note")} icon={<ChatText size={12} weight="duotone" />}>note</TabBtn>
            <TabBtn active={tab === "advance"} set={() => setTab("advance")} icon={<ArrowRight size={12} weight="duotone" />}>advance</TabBtn>
            <TabBtn active={tab === "regulator"} set={() => setTab("regulator")} icon={<Megaphone size={12} weight="duotone" />}>regulator</TabBtn>
            <TabBtn active={tab === "subjects"} set={() => setTab("subjects")} icon={<UsersThree size={12} weight="duotone" />}>subjects</TabBtn>
          </div>

          <div className="mt-3 space-y-3">
            {tab === "note" ? (
              <>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="timeline note (required)"
                  rows={3}
                  maxLength={4000}
                  className="w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                />
                <ActionBtn disabled={busy || note.trim().length === 0} onClick={() => post("/notes", { note })} label={busy ? "appending" : "append note"} />
              </>
            ) : null}

            {tab === "advance" ? (
              <>
                <select
                  value={toStatus}
                  onChange={(e) => setToStatus(e.target.value)}
                  className="rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                >
                  {statuses.filter((s) => s !== inc.status).map((s) => (
                    <option key={s} value={s}>{s.replaceAll("_", " ")}</option>                  ))}
                </select>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="justification (required for closed_no_action)"
                  rows={3}
                  maxLength={4000}
                  className="w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                />
                <ActionBtn disabled={busy} onClick={() => post("/advance", { to_status: toStatus, note })} label={busy ? "advancing" : `advance to ${toStatus.replaceAll("_", " ")}`} />
              </>
            ) : null}

            {tab === "regulator" ? (
              <>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <input
                    type="text"
                    value={regName}
                    onChange={(e) => setRegName(e.target.value)}
                    placeholder="regulator name (required)"
                    maxLength={200}
                    className="rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                  />
                  <input
                    type="text"
                    value={regRef}
                    onChange={(e) => setRegRef(e.target.value)}
                    placeholder="case reference (optional)"
                    maxLength={200}
                    className="rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                  />
                </div>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="note (optional)"
                  rows={3}
                  maxLength={4000}
                  className="w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                />
                <ActionBtn
                  disabled={busy || regName.trim().length === 0 || inc.regulator_notified_at > 0}
                  onClick={() => post("/regulator-notified", { regulator_name: regName, regulator_reference: regRef, note })}
                  label={inc.regulator_notified_at ? "already recorded" : busy ? "recording" : "record regulator notification"}
                />
              </>
            ) : null}

            {tab === "subjects" ? (
              <>
                <input
                  type="number"
                  min={0}
                  max={10000000}
                  value={affected}
                  onChange={(e) => setAffected(e.target.value)}
                  placeholder="affected count"
                  className="w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                />
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="note (optional)"
                  rows={3}
                  maxLength={4000}
                  className="w-full rounded border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:border-[var(--color-fg)]/40 focus:outline-none"
                />
                <ActionBtn
                  disabled={busy || inc.subjects_notified_at > 0}
                  onClick={() => post("/subjects-notified", { affected_count: Math.max(0, parseInt(affected || "0", 10) || 0), note })}
                  label={inc.subjects_notified_at ? "already recorded" : busy ? "recording" : "record subject notification"}
                />
              </>
            ) : null}
          </div>

          {err ? (<div className="mt-3 rounded border border-rose-500/30 bg-rose-500/5 p-2 text-xs text-rose-300">{err}</div>) : null}
        </div>
      ) : (
        <div className="mt-4 inline-flex items-center gap-2 rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-dim)]">
          <ShieldCheck size={12} weight="duotone" />
          terminal state; declare a new incident if related events surface
        </div>
      )}

      {inc.history.length > 0 ? (
        <div className="mt-6">
          <div className="text-xs uppercase tracking-wide text-[var(--color-dim)]">timeline</div>
          <ol className="mt-2 space-y-2">
            {inc.history.map((ev, i) => (
              <li key={i} className="rounded border border-[var(--color-border)] p-2 text-xs">
                <div className="flex flex-wrap items-center gap-2 text-[var(--color-dim)]">
                  <span className="font-mono">{fmtDate(ev.at)}</span>
                  <span className="font-mono">{ev.actor}</span>
                  <span className="rounded border border-[var(--color-border)] px-1 py-0.5 text-[10px] uppercase">{ev.kind}</span>
                  {ev.from_status !== ev.to_status ? (
                    <span className="font-mono text-[10px]">{ev.from_status} -&gt; {ev.to_status}</span>
                  ) : null}
                </div>
                {ev.note ? (<div className="mt-1 whitespace-pre-wrap">{ev.note}</div>) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}
