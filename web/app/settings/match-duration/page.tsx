"use client";

/**
 * Per-workspace match query duration cap administration.
 *
 * Admins pin the maximum decoded duration (in seconds) the /match
 * route will accept for this workspace. Setting the cap to 0 means
 * no restriction. When a query exceeds the cap the API returns HTTP
 * 413 with a structured body describing duration_sec and
 * max_duration_sec so the dashboard can explain why.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Clock,
  LockKey,
  Waveform,
  Warning,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface PolicyResp {
  max_duration_sec: number;
  ceiling: number;
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

function formatDuration(sec: number): string {
  if (!sec) return "off";
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

export default function MatchDurationPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [draft, setDraft] = useState<string>("0");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/match-duration", { headers: authHeaders() });
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
      setDraft(String(data.max_duration_sec));
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

  async function persist(value: number) {
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch("/api/match-duration", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ max_duration_sec: value }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : typeof body.detail === "object" && body.detail
            ? body.detail.message || JSON.stringify(body.detail)
            : `Request failed (${r.status})`;
        setSaveError(detail);
        return;
      }
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function onSave() {
    const parsed = parseInt(draft, 10);
    if (!Number.isFinite(parsed) || parsed < 0) {
      setSaveError("max_duration_sec must be a non-negative integer");
      return;
    }
    await persist(parsed);
  }

  async function onClear() {
    setDraft("0");
    await persist(0);
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
        <span className="text-zinc-200">match duration</span>
      </div>

      <header className="mb-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Clock size={26} weight="duotone" className="text-indigo-300" />
          Match query duration cap
        </h1>
        <p className="mt-2 max-w-prose text-sm text-zinc-400">
          Cap the decoded duration of a single hum or clip the matcher
          will accept for this workspace. Bytes are a poor proxy for
          compute cost: a small Opus payload can decode to many
          minutes of audio. Queries longer than the cap fail with
          HTTP 413 and a machine readable body before the matcher
          runs. Setting the cap to 0 means no restriction.
        </p>
      </header>

      {state.kind === "loading" && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <div className="space-y-3">
            {[0, 1, 2, 3].map((i) => (
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
            <div className="font-medium">Could not load match duration policy</div>
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
              <Waveform size={16} weight="duotone" /> Current policy
            </h2>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                state.data.max_duration_sec > 0
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {state.data.max_duration_sec > 0 ? "enforcing" : "no cap"}
            </span>
          </header>

          <div className="grid grid-cols-1 gap-3 px-5 py-5 sm:grid-cols-2">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3">
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                cap
              </div>
              <div className="mt-1 text-2xl font-semibold text-zinc-100 tabular-nums">
                {formatDuration(state.data.max_duration_sec)}
              </div>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3">
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                ceiling
              </div>
              <div className="mt-1 text-2xl font-semibold text-zinc-100 tabular-nums">
                {formatDuration(state.data.ceiling)}
              </div>
            </div>
          </div>

          <div className="border-t border-zinc-800 px-5 py-5">
            <label
              htmlFor="cap"
              className="block text-xs uppercase tracking-wide text-zinc-500"
            >
              max decoded duration in seconds (0 disables the cap)
            </label>
            <input
              id="cap"
              type="number"
              inputMode="numeric"
              min={0}
              max={state.data.ceiling}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="mt-2 w-40 rounded-md border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
            />
            <p className="mt-2 text-[11px] text-zinc-500">
              hard ceiling: {state.data.ceiling}s ({formatDuration(state.data.ceiling)})
            </p>
          </div>

          <footer className="flex flex-col gap-3 border-t border-zinc-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[11px] text-zinc-500">
              last updated {formatTs(state.data.updated_at)}
              {state.data.updated_by ? ` by ${state.data.updated_by}` : ""}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClear}
                disabled={saving || state.data.max_duration_sec === 0}
                className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
              >
                clear cap
              </button>
              <button
                type="button"
                onClick={onSave}
                disabled={saving}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <LockKey size={14} weight="duotone" />
                {saving ? "saving" : "save cap"}
              </button>
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
