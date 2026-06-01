"use client";

/**
 * Per-workspace webhook signing-secret maximum age (forced rotation).
 *
 * Workspace owners declare a ceiling for how long a webhook signing
 * secret may live. Any GET /webhooks response that includes a hook
 * whose secret has crossed the ceiling carries Sunset, Deprecation,
 * X-Clawhum-Webhook-Secret-Stale-Count, X-Clawhum-Webhook-Secret-Max-Age-Days
 * and optionally a Link: rel=sunset header pointing at the rotation
 * runbook. Procurement teams reading the SOC2 CC6.1 checklist see a
 * configurable cadence rather than "we will rotate eventually".
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  Key,
  Check,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  enforcing: boolean;
  max_secret_age_days: number;
  docs_url: string;
  updated_at: number;
  updated_by: string;
  stale_count: number;
  example_headers: Record<string, string>;
}

interface StaleItem {
  id: string;
  url: string;
  secret_age_days: number;
  rotated_at: number;
  created_at: number;
}

interface StaleResp {
  items: StaleItem[];
  max_secret_age_days: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: PolicyResp; stale: StaleItem[] }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

const PRESETS: { label: string; days: number }[] = [
  { label: "Off", days: 0 },
  { label: "30 days", days: 30 },
  { label: "60 days", days: 60 },
  { label: "90 days", days: 90 },
  { label: "180 days", days: 180 },
  { label: "365 days", days: 365 },
];

export default function WebhookSecretRotationPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draftDays, setDraftDays] = useState<number>(0);
  const [draftUrl, setDraftUrl] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/webhook-secret-rotation", {
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
      let stale: StaleItem[] = [];
      try {
        const sr = await fetch("/api/webhook-secret-rotation/stale", {
          headers: authHeaders(),
        });
        if (sr.ok) {
          const sj = (await sr.json()) as StaleResp;
          stale = sj.items || [];
        }
      } catch {
        // Stale list is best-effort; missing it should not block the page.
      }
      setState({ kind: "ready", data, stale });
      setDraftDays(data.max_secret_age_days);
      setDraftUrl(data.docs_url);
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

  async function onSave(nextDays: number, nextUrl: string) {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/webhook-secret-rotation", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          max_secret_age_days: nextDays,
          docs_url: nextUrl.trim(),
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : body?.detail?.message || r.statusText;
        setSaveError(detail);
        return;
      }
      await refresh();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        <ArrowLeft size={14} weight="duotone" />
        Back to settings
      </Link>

      <header className="mt-4 flex items-start gap-3">
        <Key size={28} weight="duotone" className="text-amber-600 mt-1" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Webhook secret rotation
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Declare a maximum age for webhook signing secrets. Once a hook
            crosses the ceiling, GET /webhooks responses carry Sunset and
            Deprecation headers so SDKs and dashboards can drive rotation
            before a SOC2 audit flags long-lived shared secrets.
          </p>
        </div>
      </header>

      {state.kind === "loading" && (
        <div className="mt-8 rounded-lg border border-zinc-200 p-6 text-sm text-zinc-500 dark:border-zinc-800">
          Loading policy...
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
          <Warning size={18} weight="duotone" className="mt-0.5" />
          <div>
            <div className="font-medium">
              Could not load policy ({state.status || "network"})
            </div>
            <div className="mt-1 break-all">{state.message}</div>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <section className="mt-8 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
            <div className="flex items-center gap-2 text-sm">
              <ShieldCheck
                size={16}
                weight="duotone"
                className={
                  state.data.enforcing ? "text-emerald-600" : "text-zinc-400"
                }
              />
              <span className="font-medium">
                {state.data.enforcing
                  ? `Forced rotation after ${state.data.max_secret_age_days} days`
                  : "No rotation ceiling (policy off)"}
              </span>
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              Last updated {formatTs(state.data.updated_at)}{" "}
              {state.data.updated_by ? `by ${state.data.updated_by}` : ""}
            </div>
            {state.data.enforcing && (
              <div className="mt-3 text-xs text-zinc-500">
                Stale hooks right now:{" "}
                <span
                  className={
                    state.data.stale_count > 0
                      ? "font-semibold text-amber-700"
                      : "font-semibold text-emerald-700"
                  }
                >
                  {state.data.stale_count}
                </span>
              </div>
            )}
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
            <h2 className="text-sm font-semibold">Maximum secret age</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Pick the longest a webhook signing secret may live before
              the API surfaces it as stale. Set Off to disable the policy.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {PRESETS.map((p) => {
                const active = p.days === draftDays;
                return (
                  <button
                    key={p.days}
                    type="button"
                    onClick={() => setDraftDays(p.days)}
                    className={
                      "rounded-md border px-3 py-1.5 text-xs font-medium transition " +
                      (active
                        ? "border-amber-500 bg-amber-50 text-amber-800 dark:border-amber-400 dark:bg-amber-950/30 dark:text-amber-200"
                        : "border-zinc-200 text-zinc-700 hover:border-zinc-300 dark:border-zinc-800 dark:text-zinc-300")
                    }
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
            <label className="mt-4 block text-xs text-zinc-500">
              Custom (0 to 3650 days)
              <input
                type="number"
                min={0}
                max={3650}
                value={draftDays}
                onChange={(e) =>
                  setDraftDays(Math.max(0, Math.min(3650, Number(e.target.value) || 0)))
                }
                className="mt-1 block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm dark:border-zinc-800 dark:bg-zinc-950"
              />
            </label>

            <label className="mt-4 block text-xs text-zinc-500">
              Rotation runbook URL (optional, surfaced via Link rel=sunset)
              <input
                type="url"
                placeholder="https://docs.example.com/rotate-webhooks"
                value={draftUrl}
                onChange={(e) => setDraftUrl(e.target.value)}
                className="mt-1 block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm dark:border-zinc-800 dark:bg-zinc-950"
              />
            </label>

            {saveError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
                <Warning size={14} weight="duotone" className="mt-0.5" />
                <span>{saveError}</span>
              </div>
            )}

            <div className="mt-4 flex items-center gap-3">
              <button
                type="button"
                onClick={() => onSave(draftDays, draftUrl)}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                <Check size={14} weight="duotone" />
                {saving ? "Saving..." : "Save policy"}
              </button>
              <span className="text-xs text-zinc-500">
                Requires admin + MFA on the API.
              </span>
            </div>
          </section>

          {state.data.enforcing && (
            <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
              <h2 className="text-sm font-semibold">Stale webhook secrets</h2>
              <p className="mt-1 text-xs text-zinc-500">
                Hooks whose signing secret has crossed the ceiling. Rotate
                via POST /webhooks/{"{id}"}/rotate-secret to reset the clock.
              </p>
              {state.stale.length === 0 ? (
                <div className="mt-4 rounded-md border border-dashed border-zinc-200 p-6 text-center text-xs text-zinc-500 dark:border-zinc-800">
                  No stale webhooks. Everything is within the ceiling.
                </div>
              ) : (
                <ul className="mt-4 divide-y divide-zinc-200 dark:divide-zinc-800">
                  {state.stale.map((h) => (
                    <li
                      key={h.id}
                      className="flex flex-col gap-1 py-3 text-xs sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-mono text-zinc-700 dark:text-zinc-300">
                          {h.id}
                        </div>
                        <div className="truncate text-zinc-500">{h.url}</div>
                      </div>
                      <div className="font-mono text-amber-700">
                        {h.secret_age_days} days old
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {Object.keys(state.data.example_headers).length > 0 && (
            <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
              <h2 className="text-sm font-semibold">Example response headers</h2>
              <p className="mt-1 text-xs text-zinc-500">
                When at least one hook is stale, GET /webhooks attaches:
              </p>
              <pre className="mt-3 overflow-x-auto rounded-md bg-zinc-50 p-3 text-[11px] leading-5 dark:bg-zinc-900">
                {Object.entries(state.data.example_headers)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join("\n")}
              </pre>
            </section>
          )}
        </>
      )}
    </main>
  );
}
