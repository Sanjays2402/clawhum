"use client";

/**
 * Per-workspace HTTPS-only webhook policy administration.
 *
 * When enforcement is on, every webhook create rejects plaintext
 * destinations with HTTP 400 and every delivery re-checks the
 * scheme before send. The page also warns when there are existing
 * http:// endpoints in the workspace because flipping enforcement
 * on will start failing their deliveries.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  LockKey,
  GlobeHemisphereWest,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  require_https: boolean;
  plaintext_endpoint_count: number;
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

export default function WebhookHttpsPolicyPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/webhook-policy", { headers: authHeaders() });
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

  async function setPolicy(require_https: boolean) {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/webhook-policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ require_https }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
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
        <span className="text-zinc-200">webhook policy</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <ShieldCheck size={26} weight="duotone" className="text-emerald-300" />
          Webhook transport policy
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Require https for every webhook destination in this workspace.
          When enforcement is on, plaintext URLs are rejected at create
          time and every delivery re-checks the scheme so a later policy
          flip blocks deliveries to pre-existing http endpoints.
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
            <div className="font-medium">Could not load webhook policy</div>
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
              <GlobeHemisphereWest size={16} weight="duotone" /> Require https
            </h2>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                state.data.require_https
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {state.data.require_https ? "enforcing" : "off"}
            </span>
          </header>

          <div className="space-y-4 px-5 py-5 text-sm text-zinc-300">
            <p className="leading-relaxed text-zinc-400">
              SOC2 CC6.7 and most enterprise DPAs require that webhook
              payloads (which carry signed records of customer data and
              an HMAC secret in every header) only ever cross TLS. With
              this on, http:// destinations are blocked at registration
              and at delivery time.
            </p>

            {state.data.plaintext_endpoint_count > 0 && (
              <div className="flex items-start gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs text-amber-200">
                <Warning size={16} weight="duotone" />
                <div>
                  <div className="font-medium text-amber-100">
                    {state.data.plaintext_endpoint_count} existing http
                    endpoint
                    {state.data.plaintext_endpoint_count === 1 ? "" : "s"}
                  </div>
                  <div className="mt-0.5 text-amber-200/80">
                    Turning enforcement on will start failing their
                    deliveries with code{" "}
                    <code className="text-amber-100">
                      webhook_https_required
                    </code>
                    . Migrate or remove them first.
                  </div>
                </div>
              </div>
            )}
          </div>

          <footer className="flex flex-col gap-3 border-t border-zinc-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[11px] text-zinc-500">
              last updated {formatTs(state.data.updated_at)}
              {state.data.updated_by ? ` by ${state.data.updated_by}` : ""}
            </div>
            <div className="flex items-center gap-2">
              {state.data.require_https ? (
                <button
                  type="button"
                  onClick={() => setPolicy(false)}
                  disabled={saving}
                  className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? "saving" : "turn off"}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setPolicy(true)}
                  disabled={saving}
                  className="inline-flex items-center gap-1 rounded-md bg-emerald-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <LockKey size={14} weight="duotone" />
                  {saving ? "saving" : "require https"}
                </button>
              )}
            </div>
          </footer>

          {saveError && (
            <p className="flex items-center gap-1 border-t border-zinc-800 px-5 py-3 text-xs text-rose-400">
              <Warning size={14} weight="duotone" /> {saveError}
            </p>
          )}
        </section>
      )}
    </main>
  );
}
