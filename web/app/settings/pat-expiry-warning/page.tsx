"use client";

/**
 * Per-workspace PAT expiry advance-warning policy administration.
 *
 * Workspace owners set a "warn within N days" threshold. Every
 * PAT-authenticated response whose token expires within that window
 * is decorated with Sunset and Deprecation headers so SDKs and CI
 * pipelines can rotate before the token actually stops working.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  Hourglass,
  Check,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  enforcing: boolean;
  warn_within_days: number;
  docs_url: string;
  updated_at: number;
  updated_by: string;
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
  { label: "7 days", days: 7 },
  { label: "14 days", days: 14 },
  { label: "30 days", days: 30 },
  { label: "60 days", days: 60 },
  { label: "90 days", days: 90 },
];

export default function PatExpiryWarningPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draftDays, setDraftDays] = useState<number>(0);
  const [draftUrl, setDraftUrl] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/pat-expiry-warning", {
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
      setDraftDays(data.warn_within_days);
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
      const r = await fetch("/api/pat-expiry-warning", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          warn_within_days: nextDays,
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
        <Hourglass size={28} weight="duotone" className="text-amber-600 mt-1" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            PAT expiry warning
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Tell SDKs and CI jobs to rotate before tokens stop working. When a
            personal access token is within the configured window, every
            response carries Sunset and Deprecation headers.
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
                  ? `Warning headers active within ${state.data.warn_within_days} days of expiry`
                  : "No warning headers (policy off)"}
              </span>
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              Last updated {formatTs(state.data.updated_at)}{" "}
              {state.data.updated_by ? `by ${state.data.updated_by}` : ""}
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
            <h2 className="text-sm font-semibold">Threshold</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Pick how many days before expiry the warning should start.
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
                      "rounded-md border px-3 py-1.5 text-sm transition " +
                      (active
                        ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                        : "border-zinc-200 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600")
                    }
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>

            <div className="mt-6">
              <label className="text-sm font-semibold" htmlFor="docs_url">
                Rotation runbook URL (optional)
              </label>
              <p className="mt-1 text-xs text-zinc-500">
                Surfaced to SDKs in the Link: rel=sunset response header.
              </p>
              <input
                id="docs_url"
                type="url"
                value={draftUrl}
                onChange={(e) => setDraftUrl(e.target.value)}
                placeholder="https://docs.example.com/pat-rotation"
                className="mt-2 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950"
              />
            </div>

            {saveError && (
              <div className="mt-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
                <Warning size={14} weight="duotone" className="mt-0.5" />
                <span className="break-all">{saveError}</span>
              </div>
            )}

            <div className="mt-6 flex items-center gap-3">
              <button
                type="button"
                disabled={saving}
                onClick={() => onSave(draftDays, draftUrl)}
                className="inline-flex items-center gap-1 rounded-md bg-zinc-900 px-3 py-1.5 text-sm text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
              >
                <Check size={14} weight="duotone" />
                {saving ? "Saving..." : "Save policy"}
              </button>
              <span className="text-xs text-zinc-500">
                Requires admin role and a recent MFA step-up.
              </span>
            </div>
          </section>

          {state.data.enforcing && (
            <section className="mt-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
              <h2 className="text-sm font-semibold">
                Headers your SDKs will see
              </h2>
              <p className="mt-1 text-xs text-zinc-500">
                Example for a token halfway through the warning window.
              </p>
              <pre className="mt-3 overflow-x-auto rounded-md bg-zinc-50 p-3 text-xs leading-5 dark:bg-zinc-900">
                {Object.entries(state.data.example_headers)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join("\n") || "(none)"}
              </pre>
            </section>
          )}
        </>
      )}
    </main>
  );
}
