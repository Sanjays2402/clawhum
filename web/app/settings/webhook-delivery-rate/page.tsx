"use client";

/**
 * Per-workspace per-hook outbound webhook delivery rate cap.
 *
 * Sender side ceiling on delivery attempts per minute per webhook.
 * 0 disables the cap. Suppressed attempts are written to the delivery
 * log with rate_limited=true and to the audit trail, so the workspace
 * owner can see exactly which hooks blew past the budget.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Gauge,
  ArrowLeft,
  Warning,
  ShieldCheck,
  Plugs,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  max_per_minute: number;
  ceiling: number;
  active_hook_count: number;
  updated_at: number;
  updated_by: string;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: PolicyResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

export default function WebhookDeliveryRatePage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draft, setDraft] = useState<string>("0");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/webhook-delivery-rate", {
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
      const data = (await r.json()) as PolicyResp;
      setState({ kind: "ready", data });
      setDraft(String(data.max_per_minute));
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

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      const parsed = Number.parseInt(draft, 10);
      if (!Number.isFinite(parsed) || parsed < 0) {
        setSaveError("Enter a whole number greater than or equal to 0.");
        return;
      }
      const r = await fetch("/api/webhook-delivery-rate", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ max_per_minute: parsed }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : typeof body.detail?.message === "string"
              ? body.detail.message
              : JSON.stringify(body.detail || {});
        setSaveError(detail || `Request failed (${r.status})`);
        return;
      }
      await refresh();
    } finally {
      setSaving(false);
    }
  }

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
        <span className="text-zinc-200">webhook delivery rate</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Gauge size={26} weight="duotone" className="text-emerald-300" />
          Webhook delivery rate
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Cap outbound deliveries per webhook per minute so a runaway
          producer cannot exceed the budget the receiver advertised.
          Suppressed attempts are recorded in the delivery log with
          rate_limited=true and an entry in the audit trail. Set to 0
          to disable the cap.
        </p>
      </header>

      {state.kind === "loading" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-5 w-2/3 animate-pulse rounded bg-zinc-800"
              />
            ))}
          </div>
        </div>
      )}

      {state.kind === "error" && (
        <div className="flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/5 px-5 py-6 text-sm text-rose-300">
          <Warning size={18} weight="duotone" />
          <div>
            <div className="font-medium">Could not load delivery rate policy</div>
            <div className="mt-1 text-xs text-rose-200/70">
              {state.status ? `HTTP ${state.status} ` : ""}
              {state.message}
            </div>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60">
          <header className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
            <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <ShieldCheck size={16} weight="duotone" /> Per-hook cap
            </h2>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                state.data.max_per_minute > 0
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {state.data.max_per_minute > 0
                ? `${state.data.max_per_minute}/min`
                : "no cap"}
            </span>
          </header>

          <div className="space-y-5 px-5 py-5">
            <div className="grid grid-cols-1 gap-3 text-xs text-zinc-400 sm:grid-cols-3">
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2">
                <div className="flex items-center gap-1 text-[11px] uppercase tracking-widest text-zinc-500">
                  <Plugs size={12} weight="duotone" /> active hooks
                </div>
                <div className="mt-1 text-base text-zinc-100">
                  {state.data.active_hook_count}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2">
                <div className="text-[11px] uppercase tracking-widest text-zinc-500">
                  ceiling
                </div>
                <div className="mt-1 text-base text-zinc-100">
                  {state.data.ceiling}/min
                </div>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2">
                <div className="text-[11px] uppercase tracking-widest text-zinc-500">
                  last change
                </div>
                <div className="mt-1 text-[12px] text-zinc-100">
                  {formatTs(state.data.updated_at)}
                </div>
                {state.data.updated_by && (
                  <div className="text-[11px] text-zinc-500">
                    by {state.data.updated_by}
                  </div>
                )}
              </div>
            </div>

            <div>
              <label
                htmlFor="cap"
                className="mb-1 block text-xs uppercase tracking-widest text-zinc-500"
              >
                max deliveries per minute per hook
              </label>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  id="cap"
                  type="number"
                  min={0}
                  max={state.data.ceiling}
                  inputMode="numeric"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  className="w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-emerald-400/60 sm:max-w-[160px]"
                />
                <button
                  type="button"
                  onClick={save}
                  disabled={
                    saving ||
                    String(state.data.max_per_minute) === draft.trim()
                  }
                  className="inline-flex items-center justify-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-500/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? "saving" : "save"}
                </button>
              </div>
              <p className="mt-2 text-[11px] text-zinc-500">
                0 disables the cap. The dispatcher checks the budget
                across the last 60 seconds of real attempts; previously
                suppressed records do not count.
              </p>
              {saveError && (
                <p className="mt-2 text-[12px] text-rose-300">{saveError}</p>
              )}
            </div>
          </div>
        </section>
      )}

      {state.kind === "ready" && state.data.active_hook_count === 0 && (
        <p className="mt-4 text-xs text-zinc-500">
          No active webhooks yet. The cap will apply automatically once
          you register one in{" "}
          <Link
            href="/settings/webhook-destinations"
            className="text-zinc-300 hover:text-zinc-100"
          >
            webhook destinations
          </Link>
          .
        </p>
      )}
    </main>
  );
}
