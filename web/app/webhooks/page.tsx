"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Broadcast,
  Plus,
  Trash,
  Copy,
  Check,
  ArrowClockwise,
  ArrowsClockwise,
  Key,
  PaperPlaneTilt,
  CheckCircle,
  XCircle,
  ClockClockwise,
  Warning,
  Pause,
  Play,
} from "@phosphor-icons/react/dist/ssr";

interface WebhookItem {
  id: string;
  url: string;
  events: string[];
  created_at: number;
  active: boolean;
  secret_hint: string;
  previous_secret_hint?: string | null;
  previous_secret_expires_at?: number | null;
  rotated_at?: number | null;
  paused_at?: number | null;
  resumed_at?: number | null;
}

interface RotateResponse {
  id: string;
  secret: string;
  previous_secret_expires_at: number | null;
  rotated_at: number;
}

interface CreateResponse {
  id: string;
  url: string;
  events: string[];
  secret: string;
  created_at: number;
}

interface DeliveryItem {
  id: string;
  webhook_id: string;
  event: string;
  attempt: number;
  status: number;
  ok: boolean;
  elapsed_ms: number;
  error: string | null;
  created_at: number;
  redelivery_of?: string | null;
  replayable?: boolean;
}

const ALL_EVENTS = ["match.completed"] as const;

function fmtTs(ms: number) {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export default function WebhooksPage() {
  const [items, setItems] = useState<WebhookItem[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftUrl, setDraftUrl] = useState("");
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [revealedSecret, setRevealedSecret] = useState<CreateResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<DeliveryItem[] | null>(null);
  const [delivErr, setDelivErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [rotateFor, setRotateFor] = useState<string | null>(null);
  const [rotateGrace, setRotateGrace] = useState<number>(60);
  const [rotated, setRotated] = useState<{ url: string; res: RotateResponse } | null>(null);
  const [rotateCopied, setRotateCopied] = useState(false);

  const reload = useCallback(async () => {
    setLoadErr(null);
    try {
      const r = await fetch("/api/webhooks", { cache: "no-store" });
      if (!r.ok) throw new Error(`http ${r.status}`);
      const j = await r.json();
      setItems(j.webhooks || []);
    } catch (e: any) {
      setLoadErr(e?.message || String(e));
      setItems([]);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  async function create() {
    setBusy(true);
    setCreateErr(null);
    try {
      const r = await fetch("/api/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: draftUrl, events: ["match.completed"] }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j?.detail === "string" ? j.detail : `http ${r.status}`);
      setRevealedSecret(j);
      setDraftUrl("");
      await reload();
    } catch (e: any) {
      setCreateErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this webhook? Past deliveries stay in the log.")) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/webhooks/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`http ${r.status}`);
      if (selected === id) { setSelected(null); setDeliveries(null); }
      await reload();
    } catch (e: any) {
      alert(`delete failed: ${e?.message || String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function setPaused(id: string, paused: boolean) {
    const verb = paused ? "pause" : "resume";
    const tag = `${verb}:${id}`;
    setActionBusy(tag);
    setActionMsg(null);
    try {
      const r = await fetch(`/api/webhooks/${id}/${verb}`, { method: "POST" });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`http ${r.status}${txt ? `: ${txt.slice(0, 200)}` : ""}`);
      }
      setActionMsg({
        kind: "ok",
        text: paused
          ? "webhook paused. deliveries are suspended until you resume it."
          : "webhook resumed. deliveries will fire on the next matching event.",
      });
      await reload();
    } catch (e: any) {
      setActionMsg({ kind: "err", text: `${verb} failed: ${e?.message || String(e)}` });
    } finally {
      setActionBusy(prev => (prev === tag ? null : prev));
    }
  }

  async function openDeliveries(id: string) {
    setSelected(id);
    setDeliveries(null);
    setDelivErr(null);
    try {
      const r = await fetch(`/api/webhooks/${id}/deliveries`, { cache: "no-store" });
      if (!r.ok) throw new Error(`http ${r.status}`);
      const j = await r.json();
      setDeliveries(j.deliveries || []);
    } catch (e: any) {
      setDelivErr(e?.message || String(e));
      setDeliveries([]);
    }
  }

  async function sendTest(id: string) {
    setActionBusy(`test:${id}`);
    setActionMsg(null);
    try {
      const r = await fetch(`/api/webhooks/${id}/test`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof j?.detail === "string" ? j.detail : `http ${r.status}`;
        throw new Error(detail);
      }
      setActionMsg({ kind: "ok", text: `test ping delivered (${j.delivery_id?.slice(0, 8) || "ok"})` });
      if (selected !== id) setSelected(id);
      await openDeliveries(id);
    } catch (e: any) {
      setActionMsg({ kind: "err", text: `test failed: ${e?.message || String(e)}` });
    } finally {
      setActionBusy(null);
      setTimeout(() => setActionMsg(null), 4000);
    }
  }

  async function redeliver(hookId: string, deliveryId: string) {
    setActionBusy(`re:${deliveryId}`);
    setActionMsg(null);
    try {
      const r = await fetch(
        `/api/webhooks/${hookId}/deliveries/${deliveryId}/redeliver`,
        { method: "POST" },
      );
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof j?.detail === "string" ? j.detail : `http ${r.status}`;
        throw new Error(detail);
      }
      setActionMsg({ kind: "ok", text: `redelivered (${j.delivery_id?.slice(0, 8) || "ok"})` });
      await openDeliveries(hookId);
    } catch (e: any) {
      setActionMsg({ kind: "err", text: `redeliver failed: ${e?.message || String(e)}` });
    } finally {
      setActionBusy(null);
      setTimeout(() => setActionMsg(null), 4000);
    }
  }

  async function copySecret() {
    if (!revealedSecret) return;
    try {
      await navigator.clipboard.writeText(revealedSecret.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {/* ignore */}
  }

  async function rotateSecret(id: string, url: string) {
    setActionBusy(`rot:${id}`);
    setActionMsg(null);
    try {
      const r = await fetch(`/api/webhooks/${id}/rotate-secret`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grace_seconds: Math.max(0, Math.floor(rotateGrace)) * 60 }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof j?.detail === "string" ? j.detail : `http ${r.status}`;
        throw new Error(detail);
      }
      setRotated({ url, res: j as RotateResponse });
      setRotateFor(null);
      await reload();
    } catch (e: any) {
      setActionMsg({ kind: "err", text: `rotate failed: ${e?.message || String(e)}` });
    } finally {
      setActionBusy(null);
    }
  }

  async function copyRotated() {
    if (!rotated) return;
    try {
      await navigator.clipboard.writeText(rotated.res.secret);
      setRotateCopied(true);
      setTimeout(() => setRotateCopied(false), 1500);
    } catch {/* ignore */}
  }

  return (
    <div className="px-4 py-4 space-y-4 max-w-[1100px]">
      <div className="flex items-center gap-2">
        <Broadcast size={16} weight="duotone" className="text-[var(--color-phosphor)]" />
        <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
          webhooks / outbound match events
        </h1>
        <button
          onClick={reload}
          className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] inline-flex items-center gap-1.5"
        >
          <ArrowClockwise size={12} weight="duotone" /> reload
        </button>
      </div>

      <p className="font-mono text-[11px] text-[var(--color-dim)] max-w-[80ch]">
        Register a URL and we POST it the full <code>MatchResponse</code> JSON every time a hum query completes.
        Each delivery carries an <code>X-Clawhum-Signature</code> header (HMAC-SHA256 of the body using your endpoint secret).
        Failed deliveries retry with exponential backoff up to three attempts.
      </p>

      {actionMsg && (
        <div
          role="status"
          className={`font-mono text-[11px] inline-flex items-center gap-1.5 ${actionMsg.kind === "ok" ? "text-[var(--color-phosphor)]" : "text-[var(--color-amber)]"}`}
        >
          {actionMsg.kind === "ok" ? <CheckCircle size={12} weight="duotone" /> : <Warning size={12} weight="duotone" />}
          {actionMsg.text}
        </div>
      )}

      {/* Create form */}
      <div className="border border-[var(--color-line)] p-4 space-y-3 bg-[var(--color-panel)]">
        <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">register endpoint</div>
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            type="url"
            placeholder="https://example.com/hooks/clawhum"
            value={draftUrl}
            onChange={e => setDraftUrl(e.target.value)}
            className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
          />
          <button
            disabled={busy || !draftUrl}
            onClick={create}
            className="border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest hover:bg-[rgba(0,255,140,0.06)] disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
          >
            <Plus size={12} weight="duotone" /> register
          </button>
        </div>
        {createErr && (
          <div className="font-mono text-[11px] text-[var(--color-amber)] inline-flex items-center gap-1.5">
            <Warning size={12} weight="duotone" /> {createErr}
          </div>
        )}
        <div className="font-mono text-[10px] text-[var(--color-dim)]">
          subscribed events: {ALL_EVENTS.join(", ")}
        </div>
      </div>

      {/* Reveal-once secret */}
      {revealedSecret && (
        <div className="border border-[var(--color-phosphor)] p-4 space-y-2 bg-[rgba(0,255,140,0.04)]">
          <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)]">
            signing secret (shown once)
          </div>
          <div className="font-mono text-[11px] text-[var(--color-dim)]">
            store this somewhere safe. you will not see it again.
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] break-all">
              {revealedSecret.secret}
            </code>
            <button
              onClick={copySecret}
              className="border border-[var(--color-line)] px-2 py-1.5 hover:bg-[var(--color-panel)] inline-flex items-center gap-1"
              aria-label="Copy secret"
            >
              {copied ? <Check size={14} weight="duotone" className="text-[var(--color-phosphor)]" /> : <Copy size={14} weight="duotone" />}
            </button>
            <button
              onClick={() => setRevealedSecret(null)}
              className="border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
            >
              dismiss
            </button>
          </div>
        </div>
      )}

      {/* Reveal-once secret */}
      {rotated && (
        <div className="border border-[var(--color-phosphor)] p-4 space-y-2 bg-[rgba(0,255,140,0.04)]">
          <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)]">
            new signing secret (shown once) for {rotated.url}
          </div>
          <div className="font-mono text-[11px] text-[var(--color-dim)]">
            {rotated.res.previous_secret_expires_at
              ? `previous secret stays valid until ${fmtTs(rotated.res.previous_secret_expires_at * 1000)}. deploy this key on your receiver before then.`
              : "previous secret has been invalidated immediately."}
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] break-all">
              {rotated.res.secret}
            </code>
            <button
              onClick={copyRotated}
              className="border border-[var(--color-line)] px-2 py-1.5 hover:bg-[var(--color-panel)] inline-flex items-center gap-1"
              aria-label="Copy new secret"
            >
              {rotateCopied ? <Check size={14} weight="duotone" className="text-[var(--color-phosphor)]" /> : <Copy size={14} weight="duotone" />}
            </button>
            <button
              onClick={() => setRotated(null)}
              className="border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
            >
              dismiss
            </button>
          </div>
        </div>
      )}

      {/* List */}
      <div className="border border-[var(--color-line)]">
        <div className="border-b border-[var(--color-line)] px-3 py-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          registered ({items?.length ?? 0})
        </div>

        {loadErr && (
          <div className="px-3 py-3 font-mono text-[11px] text-[var(--color-amber)] inline-flex items-center gap-1.5">
            <Warning size={12} weight="duotone" /> {loadErr}
          </div>
        )}

        {items === null && !loadErr && (
          <div className="divide-y divide-[var(--color-line)]">
            {[0, 1].map(i => (
              <div key={i} className="px-3 py-3 animate-pulse">
                <div className="h-3 w-2/3 bg-[var(--color-panel)]" />
                <div className="mt-2 h-2 w-1/3 bg-[var(--color-panel)]" />
              </div>
            ))}
          </div>
        )}

        {items && items.length === 0 && !loadErr && (
          <div className="px-4 py-8 text-center font-mono text-[11px] text-[var(--color-dim)]">
            no webhooks yet. register one above to start receiving events.
          </div>
        )}

        {items && items.length > 0 && (
          <ul className="divide-y divide-[var(--color-line)]">
            {items.map(w => (
              <li key={w.id} className="px-3 py-3">
                <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-[12px] text-[var(--color-text)] break-all">{w.url}</div>
                    <div className="mt-1 font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
                      id {w.id} · created {fmtTs(w.created_at * 1000)} · events {w.events.join(",")} · secret {w.secret_hint}
                    </div>
                    {!w.active && (
                      <div className="mt-1 font-mono text-[10px] text-[var(--color-amber)] uppercase tracking-widest inline-flex items-center gap-1">
                        <Pause size={10} weight="duotone" />
                        paused{w.paused_at ? ` at ${fmtTs(w.paused_at * 1000)}` : ""}. deliveries suspended.
                      </div>
                    )}
                    {w.previous_secret_hint && w.previous_secret_expires_at && (
                      <div className="mt-1 font-mono text-[10px] text-[var(--color-amber)] uppercase tracking-widest inline-flex items-center gap-1">
                        <Key size={10} weight="duotone" />
                        previous secret {w.previous_secret_hint} accepted until {fmtTs(w.previous_secret_expires_at * 1000)}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => sendTest(w.id)}
                      disabled={actionBusy === `test:${w.id}`}
                      className="border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest hover:bg-[rgba(0,255,140,0.06)] disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1"
                      title="Send a synthetic webhook.test event to this URL right now"
                    >
                      <PaperPlaneTilt size={12} weight="duotone" />
                      {actionBusy === `test:${w.id}` ? "sending" : "send test"}
                    </button>
                    <button
                      onClick={() => openDeliveries(w.id)}
                      className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] inline-flex items-center gap-1"
                    >
                      <ClockClockwise size={12} weight="duotone" /> deliveries
                    </button>
                    <button
                      onClick={() => { setRotateFor(rotateFor === w.id ? null : w.id); setRotateGrace(60); }}
                      className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] inline-flex items-center gap-1"
                      title="Generate a new signing secret with an optional overlap window"
                    >
                      <Key size={12} weight="duotone" /> rotate
                    </button>
                    <button
                      onClick={() => setPaused(w.id, w.active)}
                      disabled={actionBusy === `pause:${w.id}` || actionBusy === `resume:${w.id}`}
                      className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] disabled:opacity-30 inline-flex items-center gap-1"
                      title={w.active ? "Suspend deliveries without deleting this endpoint" : "Re-enable deliveries"}
                    >
                      {w.active ? (
                        <><Pause size={12} weight="duotone" /> {actionBusy === `pause:${w.id}` ? "pausing" : "pause"}</>
                      ) : (
                        <><Play size={12} weight="duotone" /> {actionBusy === `resume:${w.id}` ? "resuming" : "resume"}</>
                      )}
                    </button>
                    <button
                      onClick={() => remove(w.id)}
                      disabled={busy}
                      className="border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-amber)] hover:bg-[rgba(245,158,11,0.06)] disabled:opacity-30 inline-flex items-center gap-1"
                    >
                      <Trash size={12} weight="duotone" /> delete
                    </button>
                  </div>
                </div>

                {rotateFor === w.id && (
                  <div className="mt-3 border border-[var(--color-phosphor)] p-3 bg-[rgba(0,255,140,0.04)] space-y-2">
                    <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)]">
                      rotate signing secret
                    </div>
                    <p className="font-mono text-[11px] text-[var(--color-dim)] max-w-[72ch]">
                      Generates a new secret and returns it once. Within the overlap window
                      we attach both <code>X-Clawhum-Signature</code> and
                      <code> X-Clawhum-Signature-Previous</code> so your receiver can deploy
                      the new key without dropping deliveries. Set the window to 0 to
                      invalidate the old secret immediately.
                    </p>
                    <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                      <label className="font-mono text-[11px] text-[var(--color-muted)] inline-flex items-center gap-2">
                        overlap (minutes)
                        <input
                          type="number"
                          min={0}
                          max={10080}
                          value={rotateGrace}
                          onChange={e => setRotateGrace(Number(e.target.value))}
                          className="w-24 bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
                        />
                      </label>
                      <div className="flex gap-2 sm:ml-auto">
                        <button
                          onClick={() => setRotateFor(null)}
                          className="border border-[var(--color-line)] px-3 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
                        >
                          cancel
                        </button>
                        <button
                          disabled={actionBusy === `rot:${w.id}`}
                          onClick={() => rotateSecret(w.id, w.url)}
                          className="border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-3 py-1 font-mono text-[10px] uppercase tracking-widest hover:bg-[rgba(0,255,140,0.06)] disabled:opacity-30 inline-flex items-center gap-1"
                        >
                          <Key size={12} weight="duotone" />
                          {actionBusy === `rot:${w.id}` ? "rotating" : "rotate secret"}
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {selected === w.id && (
                  <div className="mt-3 border border-[var(--color-line)] bg-[var(--color-bg)]">
                    <div className="px-3 py-2 border-b border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                      delivery log
                    </div>
                    {delivErr && (
                      <div className="px-3 py-2 font-mono text-[11px] text-[var(--color-amber)]">{delivErr}</div>
                    )}
                    {deliveries === null && !delivErr && (
                      <div className="px-3 py-2 font-mono text-[11px] text-[var(--color-dim)]">loading…</div>
                    )}
                    {deliveries && deliveries.length === 0 && (
                      <div className="px-3 py-3 font-mono text-[11px] text-[var(--color-dim)]">
                        no deliveries yet. run a match to trigger one.
                      </div>
                    )}
                    {deliveries && deliveries.length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="w-full font-mono text-[11px]">
                          <thead className="text-[var(--color-dim)] uppercase tracking-widest text-[10px]">
                            <tr>
                              <th className="text-left px-3 py-1.5">when</th>
                              <th className="text-left px-3 py-1.5">event</th>
                              <th className="text-left px-3 py-1.5">attempt</th>
                              <th className="text-left px-3 py-1.5">status</th>
                              <th className="text-left px-3 py-1.5">latency</th>
                              <th className="text-left px-3 py-1.5">error</th>
                              <th className="text-left px-3 py-1.5">replay</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[var(--color-line)]">
                            {deliveries.map(d => (
                              <tr key={d.id}>
                                <td className="px-3 py-1.5 text-[var(--color-muted)] whitespace-nowrap">{fmtTs(d.created_at * 1000)}</td>
                                <td className="px-3 py-1.5 text-[var(--color-text)]">{d.event}</td>
                                <td className="px-3 py-1.5 text-[var(--color-muted)]">{d.attempt}</td>
                                <td className="px-3 py-1.5">
                                  <span className={`inline-flex items-center gap-1 ${d.ok ? "text-[var(--color-phosphor)]" : "text-[var(--color-amber)]"}`}>
                                    {d.ok ? <CheckCircle size={12} weight="duotone" /> : <XCircle size={12} weight="duotone" />}
                                    {d.status || "—"}
                                  </span>
                                </td>
                                <td className="px-3 py-1.5 text-[var(--color-muted)]">{d.elapsed_ms}ms</td>
                                <td className="px-3 py-1.5 text-[var(--color-dim)] max-w-[280px] truncate" title={d.error || ""}>{d.error || ""}</td>
                                <td className="px-3 py-1.5">
                                  {d.replayable ? (
                                    <button
                                      onClick={() => redeliver(w.id, d.id)}
                                      disabled={actionBusy === `re:${d.id}`}
                                      className="inline-flex items-center gap-1 text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-30 disabled:cursor-not-allowed"
                                      title="Replay this delivery using the original payload"
                                    >
                                      <ArrowsClockwise size={12} weight="duotone" />
                                      {actionBusy === `re:${d.id}` ? "sending" : "redeliver"}
                                    </button>
                                  ) : (
                                    <span className="text-[var(--color-dim)]" title={d.redelivery_of ? `replay of ${d.redelivery_of}` : "no stored payload"}>
                                      {d.redelivery_of ? "replay" : "—"}
                                    </span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Verifier snippet */}
      <details className="border border-[var(--color-line)]">
        <summary className="px-3 py-2 cursor-pointer font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)] hover:text-[var(--color-text)]">
          verify signature (node example)
        </summary>
        <pre className="px-3 py-2 overflow-x-auto bg-[var(--color-bg)] font-mono text-[11px] text-[var(--color-text)] leading-relaxed">
{`import crypto from "node:crypto";

export function verifyClawhum(rawBody: Buffer, header: string, secret: string) {
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(header));
}`}
        </pre>
      </details>
    </div>
  );
}
