"use client";

/**
 * Workspace audit log forwarding.
 *
 * Lets an admin point this workspace's audit stream at their own
 * SIEM or HTTPS collector. The page shows the current destination,
 * last delivery health, recent delivery log, and lets the admin
 * rotate (re-PUT), pause, resume, send a synthetic test event, and
 * replay specific failed deliveries.
 *
 * Strictly read your own workspace; the API enforces tenant scoping
 * server side. The signing secret is shown exactly once after PUT.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Broadcast,
  PlugsConnected,
  PaperPlaneTilt,
  ArrowsClockwise,
  Pause,
  Play,
  Trash,
  Warning,
  Copy,
  CheckCircle,
  XCircle,
  Clock,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface Destination {
  id: string;
  url: string;
  enabled: boolean;
  secret_hint: string;
  created_at: number;
  updated_at: number;
  last_attempt_at: number;
  last_success_at: number;
  last_status: number;
  last_error: string;
}

interface StatusResp {
  configured: boolean;
  destination: Destination | null;
}

interface DeliveryRow {
  delivery_id: string;
  destination_id: string;
  event_ts: number;
  attempt: number;
  status: "delivered" | "failed" | "dropped" | string;
  http_status: number;
  error: string;
  duration_ms: number;
  request_id: string | null;
  event_path: string;
  event_method: string;
  event_actor: string;
}

interface DeliveriesResp {
  items: DeliveryRow[];
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (k) h["X-API-Key"] = k;
  return h;
}

function fmtRel(ts: number): string {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

export default function AuditForwardingPage() {
  useApiKey();
  const [status, setStatus] = useState<StatusResp | null>(null);
  const [deliveries, setDeliveries] = useState<DeliveryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [testSecret, setTestSecret] = useState("");
  const [secretCopied, setSecretCopied] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/audit-forwarding", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(`${r.status} ${txt || r.statusText}`);
      }
      const data: StatusResp = await r.json();
      setStatus(data);
      if (data.configured) {
        const rd = await fetch("/api/audit-forwarding/deliveries?limit=50", {
          headers: authHeaders(),
          cache: "no-store",
        });
        if (rd.ok) {
          const dd: DeliveriesResp = await rd.json();
          setDeliveries(dd.items || []);
        }
      } else {
        setDeliveries([]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function save() {
    setBusy("save");
    setActionMsg(null);
    setActionErr(null);
    setNewSecret(null);
    try {
      const r = await fetch("/api/audit-forwarding", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ url }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body?.detail || `${r.status}`);
      setNewSecret(body.secret);
      setTestSecret(body.secret);
      setUrl("");
      setActionMsg("Destination saved. Copy the secret now, it will not be shown again.");
      await refresh();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function toggle(enabled: boolean) {
    setBusy(enabled ? "enable" : "disable");
    setActionMsg(null);
    setActionErr(null);
    try {
      const path = enabled ? "/api/audit-forwarding/enable" : "/api/audit-forwarding/disable";
      const r = await fetch(path, { method: "POST", headers: authHeaders() });
      if (!r.ok) throw new Error(`${r.status}`);
      setActionMsg(enabled ? "Forwarding resumed." : "Forwarding paused.");
      await refresh();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function removeDest() {
    if (!confirm("Remove this destination? Audit events will stop forwarding.")) return;
    setBusy("delete");
    setActionMsg(null);
    setActionErr(null);
    try {
      const r = await fetch("/api/audit-forwarding", {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!r.ok && r.status !== 204) throw new Error(`${r.status}`);
      setActionMsg("Destination removed.");
      setNewSecret(null);
      setTestSecret("");
      await refresh();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function sendTest() {
    if (!testSecret) {
      setActionErr("Paste the signing secret to run a test delivery.");
      return;
    }
    setBusy("test");
    setActionMsg(null);
    setActionErr(null);
    try {
      const r = await fetch("/api/audit-forwarding/test", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ secret: testSecret }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body?.detail || `${r.status}`);
      setActionMsg(
        body.http_status >= 200 && body.http_status < 300
          ? `Test delivered with HTTP ${body.http_status} in ${body.duration_ms} ms.`
          : `Test attempted, HTTP ${body.http_status}: ${body.error || "no body"}`
      );
      await refresh();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function replay(id: string) {
    setBusy(`replay:${id}`);
    setActionMsg(null);
    setActionErr(null);
    try {
      const r = await fetch("/api/audit-forwarding/replay", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ delivery_id: id }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body?.detail || `${r.status}`);
      setActionMsg(`Replay enqueued for delivery ${id}.`);
      setTimeout(refresh, 1500);
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  function copySecret() {
    if (!newSecret) return;
    navigator.clipboard?.writeText(newSecret).then(() => {
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 1500);
    });
  }

  const d = status?.destination ?? null;

  return (
    <div className="min-h-screen bg-[var(--color-bg)] text-[var(--color-fg)]">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:px-8 space-y-6">
        <header className="flex items-center justify-between gap-3 border-b border-[var(--color-line)] pb-4">
          <div className="flex items-center gap-3">
            <Link
              href="/admin"
              className="text-xs font-mono text-[var(--color-dim)] hover:text-[var(--color-fg)] inline-flex items-center gap-1"
            >
              <ArrowLeft size={12} weight="duotone" /> admin
            </Link>
            <h1 className="text-sm font-mono uppercase tracking-widest flex items-center gap-2">
              <Broadcast size={16} weight="duotone" />
              audit forwarding
            </h1>
          </div>
          <button
            type="button"
            onClick={refresh}
            className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)] hover:text-[var(--color-fg)] inline-flex items-center gap-1"
            disabled={loading}
          >
            <ArrowsClockwise size={12} weight="duotone" /> refresh
          </button>
        </header>

        <p className="text-xs text-[var(--color-dim)] leading-relaxed max-w-2xl">
          Stream this workspace audit log to your own SIEM or HTTPS collector.
          Each event is signed with HMAC-SHA256 over the raw request body
          using your destination secret. Verify the X-ClawHum-Signature
          header on receipt to confirm authenticity.
        </p>

        {actionMsg ? (
          <div className="rounded border border-emerald-700/40 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300 font-mono">
            {actionMsg}
          </div>
        ) : null}
        {actionErr ? (
          <div className="rounded border border-rose-700/40 bg-rose-950/30 px-3 py-2 text-xs text-rose-300 font-mono flex items-start gap-2">
            <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
            <span>{actionErr}</span>
          </div>
        ) : null}

        {newSecret ? (
          <div className="rounded border border-amber-700/50 bg-amber-950/30 p-3 space-y-2">
            <div className="text-[11px] font-mono uppercase tracking-widest text-amber-300">
              signing secret, shown once
            </div>
            <div className="font-mono text-xs break-all bg-black/40 rounded p-2 border border-[var(--color-line)]">
              {newSecret}
            </div>
            <button
              type="button"
              onClick={copySecret}
              className="text-[11px] font-mono uppercase tracking-widest border border-[var(--color-line)] rounded px-2 py-1 inline-flex items-center gap-1 hover:bg-white/5"
            >
              <Copy size={12} weight="duotone" /> {secretCopied ? "copied" : "copy"}
            </button>
            <p className="text-[11px] text-[var(--color-dim)]">
              Store this in your receiver. We only retain a hash, so we cannot
              show it again. To rotate, save a new URL.
            </p>
          </div>
        ) : null}

        <section className="rounded border border-[var(--color-line)] p-4 space-y-3">
          <h2 className="text-xs font-mono uppercase tracking-widest text-[var(--color-dim)]">
            destination
          </h2>
          {loading && !status ? (
            <div className="text-xs text-[var(--color-dim)] font-mono">loading...</div>
          ) : error ? (
            <div className="text-xs text-rose-300 font-mono">{error}</div>
          ) : d ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs font-mono">
              <div>
                <div className="text-[var(--color-dim)] text-[10px] uppercase">url</div>
                <div className="break-all">{d.url}</div>
              </div>
              <div>
                <div className="text-[var(--color-dim)] text-[10px] uppercase">id</div>
                <div>{d.id}</div>
              </div>
              <div>
                <div className="text-[var(--color-dim)] text-[10px] uppercase">state</div>
                <div className={d.enabled ? "text-emerald-300" : "text-amber-300"}>
                  {d.enabled ? "active" : "paused"} · secret ····{d.secret_hint}
                </div>
              </div>
              <div>
                <div className="text-[var(--color-dim)] text-[10px] uppercase">last attempt</div>
                <div>
                  {d.last_attempt_at ? (
                    <>
                      {fmtRel(d.last_attempt_at)} ·{" "}
                      <span
                        className={
                          d.last_status >= 200 && d.last_status < 300
                            ? "text-emerald-300"
                            : "text-rose-300"
                        }
                      >
                        {d.last_status || "no response"}
                      </span>
                    </>
                  ) : (
                    "never"
                  )}
                </div>
              </div>
              <div>
                <div className="text-[var(--color-dim)] text-[10px] uppercase">last success</div>
                <div>{fmtRel(d.last_success_at)}</div>
              </div>
              {d.last_error ? (
                <div className="sm:col-span-2">
                  <div className="text-[var(--color-dim)] text-[10px] uppercase">last error</div>
                  <div className="text-rose-300 break-all">{d.last_error}</div>
                </div>
              ) : null}
              <div className="sm:col-span-2 flex flex-wrap gap-2 pt-2 border-t border-[var(--color-line)]">
                <button
                  type="button"
                  onClick={() => toggle(!d.enabled)}
                  disabled={!!busy}
                  className="text-[11px] font-mono uppercase tracking-widest border border-[var(--color-line)] rounded px-2 py-1 inline-flex items-center gap-1 hover:bg-white/5"
                >
                  {d.enabled ? <Pause size={12} weight="duotone" /> : <Play size={12} weight="duotone" />}
                  {d.enabled ? "pause" : "resume"}
                </button>
                <button
                  type="button"
                  onClick={removeDest}
                  disabled={!!busy}
                  className="text-[11px] font-mono uppercase tracking-widest border border-rose-700/40 text-rose-300 rounded px-2 py-1 inline-flex items-center gap-1 hover:bg-rose-950/30"
                >
                  <Trash size={12} weight="duotone" /> remove
                </button>
              </div>
            </div>
          ) : (
            <div className="text-xs text-[var(--color-dim)] font-mono">
              No destination configured. Add an HTTPS sink below to start forwarding.
            </div>
          )}
        </section>

        <section className="rounded border border-[var(--color-line)] p-4 space-y-3">
          <h2 className="text-xs font-mono uppercase tracking-widest text-[var(--color-dim)] flex items-center gap-2">
            <PlugsConnected size={14} weight="duotone" />
            {d ? "rotate or replace" : "add destination"}
          </h2>
          <label className="block text-[11px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
            https url
          </label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://siem.example.com/ingest"
            className="w-full bg-black/30 border border-[var(--color-line)] rounded px-3 py-2 text-xs font-mono focus:outline-none focus:border-[var(--color-fg)]"
          />
          <button
            type="button"
            onClick={save}
            disabled={!url || !!busy}
            className="text-[11px] font-mono uppercase tracking-widest border border-[var(--color-line)] rounded px-3 py-1.5 inline-flex items-center gap-1 hover:bg-white/5 disabled:opacity-40"
          >
            {busy === "save" ? "saving..." : d ? "save and rotate secret" : "save"}
          </button>
          <p className="text-[11px] text-[var(--color-dim)]">
            Loopback, link-local, and cloud metadata hosts are rejected.
            Saving rotates the signing secret; the previous secret stops
            verifying immediately.
          </p>
        </section>

        {d ? (
          <section className="rounded border border-[var(--color-line)] p-4 space-y-3">
            <h2 className="text-xs font-mono uppercase tracking-widest text-[var(--color-dim)] flex items-center gap-2">
              <PaperPlaneTilt size={14} weight="duotone" />
              test delivery
            </h2>
            <label className="block text-[11px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
              signing secret
            </label>
            <input
              type="password"
              value={testSecret}
              onChange={(e) => setTestSecret(e.target.value)}
              placeholder="awsec_..."
              className="w-full bg-black/30 border border-[var(--color-line)] rounded px-3 py-2 text-xs font-mono focus:outline-none focus:border-[var(--color-fg)]"
            />
            <button
              type="button"
              onClick={sendTest}
              disabled={!testSecret || !!busy}
              className="text-[11px] font-mono uppercase tracking-widest border border-[var(--color-line)] rounded px-3 py-1.5 inline-flex items-center gap-1 hover:bg-white/5 disabled:opacity-40"
            >
              {busy === "test" ? "sending..." : "send test event"}
            </button>
          </section>
        ) : null}

        <section className="rounded border border-[var(--color-line)] p-4 space-y-3">
          <h2 className="text-xs font-mono uppercase tracking-widest text-[var(--color-dim)] flex items-center gap-2">
            <Clock size={14} weight="duotone" />
            recent deliveries
          </h2>
          {deliveries.length === 0 ? (
            <div className="text-xs text-[var(--color-dim)] font-mono">
              {d ? "No deliveries recorded yet." : "Configure a destination to see deliveries."}
            </div>
          ) : (
            <ul className="space-y-1 max-h-96 overflow-auto">
              {deliveries.map((row) => (
                <li
                  key={row.delivery_id}
                  className="grid grid-cols-[auto_auto_1fr_auto_auto] gap-2 items-center text-[11px] font-mono"
                >
                  {row.status === "delivered" ? (
                    <CheckCircle size={12} weight="duotone" className="text-emerald-300" />
                  ) : row.status === "dropped" ? (
                    <Warning size={12} weight="duotone" className="text-amber-300" />
                  ) : (
                    <XCircle size={12} weight="duotone" className="text-rose-300" />
                  )}
                  <span className="text-[var(--color-dim)]">{row.http_status || "no resp"}</span>
                  <span className="truncate">
                    {row.event_method} {row.event_path}
                  </span>
                  <span className="text-[var(--color-dim)]">{fmtRel(row.event_ts)}</span>
                  <button
                    type="button"
                    onClick={() => replay(row.delivery_id)}
                    disabled={!!busy}
                    className="text-[10px] uppercase tracking-widest border border-[var(--color-line)] rounded px-1.5 py-0.5 hover:bg-white/5"
                  >
                    replay
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
