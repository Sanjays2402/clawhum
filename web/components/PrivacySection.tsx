"use client";

// /settings -> Privacy & data
//
// Exposes the two GDPR endpoints already shipped by the FastAPI service:
//   * GET    /api/v1/privacy/export   -> downloads a JSON dump
//   * DELETE /api/v1/privacy/me       -> redacts the caller's data
//
// The export turns into a real .json file via Blob + anchor click. The
// delete flow requires the user to type ERASE and re-confirm, then
// shows the redaction counts returned by the backend.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Download,
  ShieldWarning,
  Trash,
  ArrowsClockwise,
  Check,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey } from "@/lib/apiKey";
import {
  ERASE_CONFIRMATION,
  exportFilename,
  isEraseConfirmed,
  normaliseErase,
  summarise,
  type EraseResult,
  type ExportSummary,
} from "@/lib/privacy";

type ExportState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; summary: ExportSummary; at: number }
  | { kind: "error"; message: string };

type EraseState =
  | { kind: "idle" }
  | { kind: "confirming" }
  | { kind: "running" }
  | { kind: "ok"; result: EraseResult; at: number }
  | { kind: "error"; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function triggerDownload(filename: string, text: string) {
  if (typeof window === "undefined") return;
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export default function PrivacySection() {
  const [exportState, setExportState] = useState<ExportState>({ kind: "idle" });
  const [eraseState, setEraseState] = useState<EraseState>({ kind: "idle" });
  const [confirmText, setConfirmText] = useState("");
  const confirmed = useMemo(() => isEraseConfirmed(confirmText), [confirmText]);

  // Reset the confirmation textbox whenever we leave the confirming step.
  useEffect(() => {
    if (eraseState.kind !== "confirming") setConfirmText("");
  }, [eraseState.kind]);

  const runExport = useCallback(async () => {
    setExportState({ kind: "loading" });
    try {
      const r = await fetch("/api/v1/privacy/export", {
        method: "GET",
        headers: { Accept: "application/json", ...authHeaders() },
        cache: "no-store",
      });
      const raw = await r.text();
      if (!r.ok) {
        setExportState({ kind: "error", message: `export failed (${r.status})` });
        return;
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        setExportState({ kind: "error", message: "export returned invalid JSON" });
        return;
      }
      const summary = summarise(parsed as never, raw);
      triggerDownload(exportFilename(), raw);
      setExportState({ kind: "ok", summary, at: Date.now() });
    } catch (e) {
      setExportState({
        kind: "error",
        message: e instanceof Error ? e.message : "network error",
      });
    }
  }, []);

  const runErase = useCallback(async () => {
    setEraseState({ kind: "running" });
    try {
      const r = await fetch("/api/v1/privacy/me", {
        method: "DELETE",
        headers: { Accept: "application/json", ...authHeaders() },
      });
      const text = await r.text();
      if (!r.ok) {
        setEraseState({
          kind: "error",
          message: `erase failed (${r.status})${text ? `: ${text.slice(0, 120)}` : ""}`,
        });
        return;
      }
      let body: Record<string, unknown> = {};
      try {
        body = text ? (JSON.parse(text) as Record<string, unknown>) : {};
      } catch {
        body = {};
      }
      setEraseState({ kind: "ok", result: normaliseErase(body), at: Date.now() });
    } catch (e) {
      setEraseState({
        kind: "error",
        message: e instanceof Error ? e.message : "network error",
      });
    }
  }, []);

  return (
    <section className="panel rounded-[2px] p-4 space-y-4">
      <div className="flex items-center gap-2">
        <ShieldWarning size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
        <span className="label-xs">privacy &amp; data</span>
        <span className="ml-auto font-mono text-[10px] text-[var(--color-dim)]">gdpr</span>
      </div>
      <p className="font-mono text-[11px] text-[var(--color-muted)] leading-relaxed">
        download every audit and feedback row attributable to your api key, or
        redact them server side. both actions affect only data linked to the key
        currently saved in this browser.
      </p>

      {/* Export */}
      <div className="border border-[var(--color-line)] rounded-[2px] p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Download size={12} weight="duotone" />
          <span className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]">
            export my data
          </span>
          <button
            type="button"
            onClick={runExport}
            disabled={exportState.kind === "loading"}
            className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {exportState.kind === "loading" ? (
              <>
                <ArrowsClockwise size={11} weight="duotone" className="animate-spin" />
                preparing
              </>
            ) : (
              <>
                <Download size={11} weight="duotone" />
                download json
              </>
            )}
          </button>
        </div>
        {exportState.kind === "ok" && (
          <div className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            saved {fmtBytes(exportState.summary.bytes)} containing{" "}
            <span className="text-[var(--color-text)]">
              {exportState.summary.audit}
            </span>{" "}
            audit events and{" "}
            <span className="text-[var(--color-text)]">
              {exportState.summary.feedback}
            </span>{" "}
            feedback rows for actor{" "}
            <span className="text-[var(--color-text)]">{exportState.summary.actor.slice(0, 12)}</span>.
          </div>
        )}
        {exportState.kind === "error" && (
          <div className="font-mono text-[10px] text-red-400 leading-relaxed">
            {exportState.message}
          </div>
        )}
        {exportState.kind === "idle" && (
          <div className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            returns a portable json file you can hand to any tool or store offline.
          </div>
        )}
      </div>

      {/* Erase */}
      <div className="border border-red-900/50 rounded-[2px] p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Trash size={12} weight="duotone" className="text-red-400" />
          <span className="font-mono text-[11px] uppercase tracking-widest text-red-300">
            erase my data
          </span>
          {eraseState.kind === "idle" && (
            <button
              type="button"
              onClick={() => setEraseState({ kind: "confirming" })}
              className="ml-auto border border-red-900/60 px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-red-300 hover:bg-red-950/40 inline-flex items-center gap-1.5"
            >
              <Trash size={11} weight="duotone" />
              start
            </button>
          )}
          {(eraseState.kind === "ok" || eraseState.kind === "error") && (
            <button
              type="button"
              onClick={() => setEraseState({ kind: "idle" })}
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
            >
              dismiss
            </button>
          )}
        </div>

        {eraseState.kind === "idle" && (
          <div className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            redacts the actor id on every audit event and feedback row tied to your api key.
            shared result urls stay intact because they belong to the recipients.
          </div>
        )}

        {eraseState.kind === "confirming" && (
          <div className="space-y-2">
            <p className="font-mono text-[10px] text-red-300 leading-relaxed">
              type {ERASE_CONFIRMATION} to confirm. this cannot be undone.
            </p>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              autoFocus
              placeholder={ERASE_CONFIRMATION}
              className="w-full bg-[var(--color-bg)] border border-red-900/50 px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-red-500"
              aria-label="erase confirmation"
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={runErase}
                disabled={!confirmed}
                className="border border-red-900/60 bg-red-950/40 px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-red-200 disabled:opacity-40 hover:bg-red-900/40"
              >
                erase now
              </button>
              <button
                type="button"
                onClick={() => setEraseState({ kind: "idle" })}
                className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
              >
                cancel
              </button>
            </div>
          </div>
        )}

        {eraseState.kind === "running" && (
          <div className="font-mono text-[10px] text-[var(--color-muted)] inline-flex items-center gap-2">
            <ArrowsClockwise size={11} weight="duotone" className="animate-spin" />
            redacting...
          </div>
        )}

        {eraseState.kind === "ok" && (
          <div className="font-mono text-[10px] text-[var(--color-muted)] leading-relaxed inline-flex items-center gap-2">
            <Check size={11} weight="duotone" className="text-emerald-400" />
            redacted {eraseState.result.redacted_events} audit events and{" "}
            {eraseState.result.redacted_feedback_rows} feedback rows.
          </div>
        )}

        {eraseState.kind === "error" && (
          <div className="font-mono text-[10px] text-red-400 leading-relaxed">
            {eraseState.message}
          </div>
        )}
      </div>
    </section>
  );
}
