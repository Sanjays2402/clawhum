"use client";

/**
 * Audit log tamper-evidence verification.
 *
 * Re-derives the SHA-256 hash chain across every audit log file on
 * disk and shows whether any entry was edited, deleted, or reordered
 * after the fact. The backend exposes the read-only /audit/verify
 * endpoint; this page is the procurement-facing surface that lets an
 * enterprise admin (or a SOC2 reviewer sitting next to one) prove the
 * chain holds without dropping to curl.
 *
 * Admin only. The endpoint itself enforces the role; this page also
 * surfaces a clear 403 message when a non-admin tries to load it.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowsClockwise,
  CheckCircle,
  XCircle,
  Warning,
  ShieldCheck,
  FileText,
  Copy,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface ChainFile {
  path: string;
  entries: number;
  valid: number;
  ok: boolean;
  first_bad_line: number | null;
  reason: string | null;
  head_prev_hash: string | null;
  tail_entry_hash: string | null;
}

interface VerifyResp {
  ok: boolean;
  files: ChainFile[];
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: VerifyResp; fetchedAt: number }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (k) h["X-API-Key"] = k;
  return h;
}

function shortHash(h: string | null): string {
  if (!h) return "none";
  if (h === "0".repeat(64)) return "genesis (00…00)";
  return `${h.slice(0, 10)}…${h.slice(-6)}`;
}

function fmtTime(ts: number): string {
  if (!ts) return "never";
  const d = new Date(ts);
  return d.toLocaleString();
}

export default function AuditChainPage() {
  useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [includeRotated, setIncludeRotated] = useState(true);
  const [copied, setCopied] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const qs = includeRotated ? "?include_rotated=true" : "?include_rotated=false";
      const r = await fetch(`/api/audit/verify${qs}`, {
        headers: authHeaders(),
        cache: "no-store",
      });
      const txt = await r.text();
      if (!r.ok) {
        let msg = txt || r.statusText;
        try {
          const j = JSON.parse(txt);
          msg = j.detail || j.message || msg;
        } catch {
          // raw text already useful
        }
        setState({ kind: "error", status: r.status, message: String(msg) });
        return;
      }
      const data = JSON.parse(txt) as VerifyResp;
      setState({ kind: "ok", data, fetchedAt: Date.now() });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setState({ kind: "error", status: 0, message: msg });
    }
  }, [includeRotated]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied((c) => (c === key ? null : c)), 1200);
    } catch {
      // clipboard blocked; ignore
    }
  };

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:py-12">
      <div className="mb-6 flex items-center justify-between gap-3">
        <Link
          href="/admin"
          className="inline-flex items-center gap-1 text-xs text-[var(--color-dim)] hover:text-[var(--color-fg)]"
        >
          <ArrowLeft size={12} weight="duotone" />
          back to admin
        </Link>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={state.kind === "loading"}
          className="inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-xs hover:bg-[var(--color-bg-soft)] disabled:opacity-50"
        >
          <ArrowsClockwise
            size={12}
            weight="duotone"
            className={state.kind === "loading" ? "animate-spin" : ""}
          />
          re-verify
        </button>
      </div>

      <header className="mb-6">
        <div className="flex items-center gap-2">
          <ShieldCheck size={18} weight="duotone" className="text-[var(--color-fg)]" />
          <h1 className="text-lg font-semibold">audit log integrity</h1>
        </div>
        <p className="mt-2 max-w-2xl text-xs text-[var(--color-dim)]">
          Every audit log entry carries a SHA-256 of its payload plus the
          previous entry hash. Re-deriving the chain proves no row was
          edited, deleted, or reordered after the fact. Run this before a
          procurement review, after a server move, or on a schedule from
          your SIEM against the same JSON endpoint at{" "}
          <code className="text-[var(--color-fg)]">GET /audit/verify</code>.
        </p>
      </header>

      <label className="mb-4 flex items-center gap-2 text-xs text-[var(--color-dim)]">
        <input
          type="checkbox"
          checked={includeRotated}
          onChange={(e) => setIncludeRotated(e.target.checked)}
          className="h-3 w-3"
        />
        walk rotated log siblings too
      </label>

      {state.kind === "loading" && (
        <div className="rounded border border-[var(--color-border)] p-6 text-center text-xs text-[var(--color-dim)]">
          verifying chain…
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded border border-rose-500/40 bg-rose-500/5 p-4 text-xs text-rose-300">
          <div className="flex items-center gap-2 font-medium">
            <Warning size={14} weight="duotone" />
            {state.status === 403
              ? "admin role required"
              : state.status === 401
              ? "sign in with an admin API key"
              : `verify failed (${state.status || "network"})`}
          </div>
          <div className="mt-1 text-rose-300/80">{state.message}</div>
        </div>
      )}

      {state.kind === "ok" && (
        <>
          <div
            className={`mb-6 rounded border p-4 ${
              state.data.ok
                ? "border-emerald-500/40 bg-emerald-500/5 text-emerald-300"
                : "border-rose-500/40 bg-rose-500/5 text-rose-300"
            }`}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              {state.data.ok ? (
                <CheckCircle size={16} weight="duotone" />
              ) : (
                <XCircle size={16} weight="duotone" />
              )}
              {state.data.ok
                ? "chain intact across every file"
                : "chain broken on at least one file"}
            </div>
            <div className="mt-1 text-[11px] opacity-80">
              verified {fmtTime(state.fetchedAt)} ·{" "}
              {state.data.files.length} file
              {state.data.files.length === 1 ? "" : "s"} scanned
            </div>
          </div>

          {state.data.files.length === 0 ? (
            <div className="rounded border border-[var(--color-border)] p-6 text-center text-xs text-[var(--color-dim)]">
              no audit log files on disk yet. The first mutating request
              will create one.
            </div>
          ) : (
            <ul className="space-y-3">
              {state.data.files.map((f) => (
                <li
                  key={f.path}
                  className={`rounded border p-4 ${
                    f.ok
                      ? "border-[var(--color-border)]"
                      : "border-rose-500/40 bg-rose-500/5"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs">
                        <FileText
                          size={12}
                          weight="duotone"
                          className="shrink-0 text-[var(--color-dim)]"
                        />
                        <span className="truncate font-mono">{f.path}</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] text-[var(--color-dim)] sm:grid-cols-4">
                        <span>
                          entries:{" "}
                          <span className="text-[var(--color-fg)]">
                            {f.entries}
                          </span>
                        </span>
                        <span>
                          valid:{" "}
                          <span
                            className={
                              f.valid === f.entries
                                ? "text-emerald-300"
                                : "text-rose-300"
                            }
                          >
                            {f.valid}
                          </span>
                        </span>
                        <span>
                          first bad:{" "}
                          <span className="text-[var(--color-fg)]">
                            {f.first_bad_line ?? "—"}
                          </span>
                        </span>
                        <span>
                          status:{" "}
                          {f.ok ? (
                            <span className="text-emerald-300">ok</span>
                          ) : (
                            <span className="text-rose-300">broken</span>
                          )}
                        </span>
                      </div>
                    </div>
                  </div>

                  {f.reason && (
                    <div className="mt-2 rounded bg-rose-500/10 px-2 py-1 text-[11px] text-rose-300">
                      reason: {f.reason}
                    </div>
                  )}

                  <div className="mt-3 grid gap-2 text-[11px] sm:grid-cols-2">
                    <HashRow
                      label="head prev_hash"
                      value={f.head_prev_hash}
                      onCopy={(v) => void copy(v, `${f.path}#head`)}
                      copied={copied === `${f.path}#head`}
                    />
                    <HashRow
                      label="tail entry_hash"
                      value={f.tail_entry_hash}
                      onCopy={(v) => void copy(v, `${f.path}#tail`)}
                      copied={copied === `${f.path}#tail`}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}

          <details className="mt-8 rounded border border-[var(--color-border)] p-3 text-xs text-[var(--color-dim)]">
            <summary className="cursor-pointer text-[var(--color-fg)]">
              verify from your own pipeline
            </summary>
            <pre className="mt-2 overflow-auto text-[11px]">
{`curl -s -H "X-API-Key: $CLAWHUM_ADMIN_KEY" \\
  "$CLAWHUM_API_URL/audit/verify?include_rotated=true" \\
  | jq '.ok, (.files | map({path, ok, first_bad_line, reason}))'`}
            </pre>
          </details>
        </>
      )}
    </main>
  );
}

function HashRow({
  label,
  value,
  onCopy,
  copied,
}: {
  label: string;
  value: string | null;
  onCopy: (v: string) => void;
  copied: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded border border-[var(--color-border)] px-2 py-1">
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wide text-[var(--color-dim)]">
          {label}
        </div>
        <div className="truncate font-mono text-[var(--color-fg)]">
          {shortHash(value)}
        </div>
      </div>
      {value ? (
        <button
          type="button"
          onClick={() => onCopy(value)}
          className="shrink-0 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] hover:bg-[var(--color-bg-soft)]"
          aria-label={`copy ${label}`}
        >
          {copied ? "copied" : <Copy size={10} weight="duotone" />}
        </button>
      ) : null}
    </div>
  );
}
