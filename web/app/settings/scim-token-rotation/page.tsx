"use client";

/**
 * Per-workspace SCIM bearer token maximum age (forced rotation).
 *
 * Workspace owners declare a ceiling for how long the active SCIM
 * bearer token may live. Once the token crosses the ceiling, every
 * SCIM 2.0 response (/scim/v2/Users, /Users/{id}, etc) carries
 * Sunset, Deprecation, X-Clawhum-SCIM-Token-Age-Days,
 * X-Clawhum-SCIM-Token-Max-Age-Days, X-Clawhum-SCIM-Token-Created-At
 * and optionally a Link: rel=sunset header pointing at the rotation
 * runbook. Procurement teams reading the SOC2 CC6.1 checklist see a
 * configurable cadence on the most powerful shared secret in the
 * platform.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  Key,
  Check,
  Clock,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  enforcing: boolean;
  max_token_age_days: number;
  docs_url: string;
  updated_at: number;
  updated_by: string;
  token_configured: boolean;
  token_created_at: number;
  token_age_days: number;
  token_is_stale: boolean;
  example_headers: Record<string, string>;
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

const PRESETS: { label: string; days: number }[] = [
  { label: "Off", days: 0 },
  { label: "30 days", days: 30 },
  { label: "60 days", days: 60 },
  { label: "90 days", days: 90 },
  { label: "180 days", days: 180 },
  { label: "365 days", days: 365 },
];

export default function ScimTokenRotationPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draftDays, setDraftDays] = useState<number>(0);
  const [draftUrl, setDraftUrl] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/scim-token-rotation", {
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
      setDraftDays(data.max_token_age_days);
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
      const r = await fetch("/api/scim-token-rotation", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          max_token_age_days: nextDays,
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
            SCIM token rotation
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Declare a maximum age for the active SCIM bearer token. Once
            the token crosses the ceiling, every SCIM 2.0 response
            carries Sunset and Deprecation headers so the buyer&rsquo;s
            IdP adapter can drive rotation before a SOC2 audit flags a
            long-lived shared secret.
          </p>
        </div>
      </header>

      {state.kind === "loading" && (
        <div
          aria-live="polite"
          className="mt-8 space-y-3"
        >
          <div className="h-16 animate-pulse rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900" />
          <div className="h-40 animate-pulse rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900" />
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
                  ? `Forced rotation after ${state.data.max_token_age_days} days`
                  : "No rotation ceiling (policy off)"}
              </span>
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              Last updated {formatTs(state.data.updated_at)}{" "}
              {state.data.updated_by ? `by ${state.data.updated_by}` : ""}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="flex items-center gap-1.5 text-zinc-500">
                  <Clock size={12} weight="duotone" />
                  Active SCIM token
                </div>
                <div className="mt-1 font-mono">
                  {state.data.token_configured
                    ? `${state.data.token_age_days} days old`
                    : "not configured"}
                </div>
                <div className="mt-1 text-zinc-500">
                  {state.data.token_configured
                    ? `minted ${formatTs(state.data.token_created_at)}`
                    : "Mint one from Admin · SCIM"}
                </div>
              </div>
              <div className="rounded-md border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="flex items-center gap-1.5 text-zinc-500">
                  <Warning size={12} weight="duotone" />
                  Status
                </div>
                <div
                  className={
                    "mt-1 font-mono " +
                    (state.data.token_is_stale
                      ? "text-amber-700"
                      : "text-emerald-700")
                  }
                >
                  {!state.data.enforcing
                    ? "policy off"
                    : !state.data.token_configured
                      ? "no token"
                      : state.data.token_is_stale
                        ? "stale, rotation due"
                        : "within ceiling"}
                </div>
                <div className="mt-1 text-zinc-500">
                  Sunset headers attach only when stale.
                </div>
              </div>
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
            <h2 className="text-sm font-semibold">Maximum token age</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Pick the longest the active SCIM bearer may live before
              the API surfaces it as stale. Set Off to disable the
              policy.
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
                  setDraftDays(
                    Math.max(0, Math.min(3650, Number(e.target.value) || 0))
                  )
                }
                className="mt-1 block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm dark:border-zinc-800 dark:bg-zinc-950"
              />
            </label>

            <label className="mt-4 block text-xs text-zinc-500">
              Rotation runbook URL (optional, surfaced via Link
              rel=sunset)
              <input
                type="url"
                placeholder="https://docs.example.com/rotate-scim"
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

          {Object.keys(state.data.example_headers).length > 0 && (
            <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
              <h2 className="text-sm font-semibold">
                Example response headers
              </h2>
              <p className="mt-1 text-xs text-zinc-500">
                When the active token is past the ceiling, every SCIM
                2.0 response attaches:
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
