"use client";

/**
 * Workspace request body size cap console.
 *
 * Security and IT teams require a hard ceiling on the request payload
 * size their workspace will accept, so a careless caller or a stolen
 * API key cannot force the API to buffer enormous documents. This
 * screen reads the current cap, shows the ceiling the API will
 * accept, and lets admins (MFA gated) raise, tighten, or remove it.
 *
 * Strictly per-workspace; the backend enforces tenant scoping.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ShieldCheck,
  Warning,
  CheckCircle,
  HardDrives,
} from "@phosphor-icons/react/dist/ssr";
import { API_BASE } from "@/lib/api";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface BodySizeView {
  max_bytes: number;
  ceiling: number;
  updated_at: number;
  updated_by: string;
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

function fmtBytes(n: number): string {
  if (!n) return "no cap";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MiB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}

const PRESETS: { label: string; bytes: number }[] = [
  { label: "No cap", bytes: 0 },
  { label: "64 KiB", bytes: 64 * 1024 },
  { label: "1 MiB", bytes: 1024 * 1024 },
  { label: "8 MiB", bytes: 8 * 1024 * 1024 },
  { label: "32 MiB", bytes: 32 * 1024 * 1024 },
];

export default function BodySizePage() {
  useApiKey();
  const [data, setData] = useState<BodySizeView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(API_BASE + "/body-size", { headers: authHeaders() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as BodySizeView;
      setData(j);
      setDraft(String(j.max_bytes));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = useCallback(
    async (bytes: number) => {
      setBusy(true);
      setMsg(null);
      setActionErr(null);
      try {
        const r = await fetch(API_BASE + "/body-size", {
          method: "PUT",
          headers: authHeaders(),
          body: JSON.stringify({ max_bytes: bytes }),
        });
        const j = await r.json().catch(() => null);
        if (!r.ok) {
          const detail = j?.detail?.message || j?.detail || `HTTP ${r.status}`;
          throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        }
        setData(j as BodySizeView);
        setDraft(String((j as BodySizeView).max_bytes));
        setMsg(bytes === 0 ? "Cap removed" : `Cap set to ${fmtBytes(bytes)}`);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : "save failed");
      } finally {
        setBusy(false);
      }
    },
    []
  );

  const submitDraft = useCallback(() => {
    const n = Number(draft);
    if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
      setActionErr("Enter a non negative integer number of bytes");
      return;
    }
    save(n);
  }, [draft, save]);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
      <div className="mb-6 flex items-center gap-3 text-sm text-[var(--color-dim)]">
        <Link
          href="/admin"
          className="inline-flex items-center gap-1 rounded-md border border-transparent px-2 py-1 hover:border-[var(--color-border)] hover:text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
        >
          <ArrowLeft size={14} weight="duotone" />
          Admin
        </Link>
      </div>

      <header className="mb-8">
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-[var(--color-border)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--color-dim)]">
          <HardDrives size={14} weight="duotone" />
          Request body size cap
        </div>
        <h1 className="text-2xl font-semibold sm:text-3xl">Workspace payload ceiling</h1>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-dim)]">
          Set a single integer limit on the request body the API will accept
          on every chargeable route in this workspace. Requests over the cap
          are rejected with HTTP 413 before the route runs, so they never
          touch the worker or count against your monthly quota.
        </p>
      </header>

      {loading ? (
        <div className="space-y-3" aria-busy="true">
          <div className="h-24 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
          <div className="h-36 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/5 p-5 text-sm text-rose-300">
          <div className="mb-1 flex items-center gap-2 font-medium">
            <Warning size={16} weight="duotone" /> Could not load policy
          </div>
          <p className="text-rose-200/80">{error}</p>
          <button
            type="button"
            onClick={load}
            className="mt-3 rounded-md border border-rose-500/40 px-3 py-1 text-xs hover:bg-rose-500/10 focus:outline-none focus:ring-2 focus:ring-rose-400"
          >
            Retry
          </button>
        </div>
      ) : !data ? (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center text-sm text-[var(--color-dim)]">
          No policy data. Try reloading.
        </div>
      ) : (
        <div className="space-y-6">
          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-xs uppercase tracking-wider text-[var(--color-dim)]">
                  Current cap
                </div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">
                  {fmtBytes(data.max_bytes)}
                </div>
                <div className="mt-1 text-xs text-[var(--color-dim)]">
                  {data.max_bytes === 0
                    ? "No per workspace cap. The 256 MiB platform ceiling still applies."
                    : `${data.max_bytes.toLocaleString("en-US")} bytes exactly`}
                </div>
              </div>
              <div className="text-right text-xs text-[var(--color-dim)]">
                <div>Updated by</div>
                <div className="font-mono text-[var(--color-text)]">
                  {data.updated_by || "unset"}
                </div>
                <div className="mt-1">{fmtDate(data.updated_at)}</div>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h2 className="text-sm font-semibold">Quick presets</h2>
            <p className="mt-1 text-xs text-[var(--color-dim)]">
              MFA step up required. Picking a preset applies it immediately.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {PRESETS.map((p) => {
                const active = data.max_bytes === p.bytes;
                return (
                  <button
                    key={p.label}
                    type="button"
                    disabled={busy || active}
                    onClick={() => save(p.bytes)}
                    className={`rounded-md border px-3 py-1.5 text-sm transition focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] ${
                      active
                        ? "border-[var(--color-accent)]/60 bg-[var(--color-accent)]/10 text-[var(--color-text)]"
                        : "border-[var(--color-border)] text-[var(--color-dim)] hover:border-[var(--color-text)]/40 hover:text-[var(--color-text)] disabled:opacity-50"
                    }`}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          </section>

          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h2 className="text-sm font-semibold">Custom value</h2>
            <p className="mt-1 text-xs text-[var(--color-dim)]">
              Bytes. 0 disables the per workspace cap. Maximum {data.ceiling.toLocaleString("en-US")} bytes.
            </p>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
              <input
                type="number"
                inputMode="numeric"
                min={0}
                max={data.ceiling}
                step={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={busy}
                aria-label="Maximum body size in bytes"
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 font-mono text-sm tabular-nums focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40 sm:max-w-xs"
              />
              <button
                type="button"
                onClick={submitDraft}
                disabled={busy || draft === String(data.max_bytes)}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-2 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-accent)]/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ShieldCheck size={14} weight="duotone" />
                {busy ? "Saving" : "Save"}
              </button>
            </div>
          </section>

          {msg && (
            <div className="flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/5 px-3 py-2 text-sm text-emerald-300">
              <CheckCircle size={14} weight="duotone" />
              {msg}
            </div>
          )}
          {actionErr && (
            <div className="flex items-center gap-2 rounded-md border border-rose-500/40 bg-rose-500/5 px-3 py-2 text-sm text-rose-300">
              <Warning size={14} weight="duotone" />
              {actionErr}
            </div>
          )}
        </div>
      )}
    </main>
  );
}
