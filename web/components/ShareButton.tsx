"use client";

import { useState } from "react";
import { ShareNetwork, Check, Copy, Warning } from "@phosphor-icons/react/dist/ssr";
import type { StoredMatch } from "@/lib/history";
import { toShareInput, type ShareInput } from "@/lib/share";
import { toast } from "@/lib/toast";

type State =
  | { kind: "idle" }
  | { kind: "creating" }
  | { kind: "ready"; url: string; copied: boolean }
  | { kind: "error"; message: string };

interface Props {
  /** Full client-side match (capture/match detail). */
  match?: StoredMatch;
  /** Server-side history row or any compact result shape. Used when no `match`. */
  input?: ShareInput;
  /** Compact icon-only variant, for dense lists like history rows. */
  compact?: boolean;
}

export default function ShareButton({ match, input, compact = false }: Props) {
  const payload: ShareInput | null = input ?? (match ? toShareInput(match) : null);
  const [state, setState] = useState<State>({ kind: "idle" });

  async function create() {
    if (!payload) {
      setState({ kind: "error", message: "nothing to share" });
      return;
    }
    setState({ kind: "creating" });
    try {
      const r = await fetch("/api/share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json().catch(() => ({} as any));
      if (!r.ok) {
        throw new Error(
          typeof j?.detail === "string" ? j.detail : `${r.status} ${r.statusText}`,
        );
      }
      const path: string = j.url_path || `/r/${j.id}`;
      const url =
        typeof window !== "undefined" ? `${window.location.origin}${path}` : path;
      await copyToClipboard(url);
      setState({ kind: "ready", url, copied: true });
      toast.success("share link copied", {
        description: url,
        action: { label: "open", onClick: () => window.open(url, "_blank", "noopener,noreferrer") },
      });
    } catch (e: any) {
      const msg = e?.message || String(e);
      setState({ kind: "error", message: msg });
      toast.error("share failed", { description: msg.slice(0, 200) });
    }
  }

  async function copyAgain() {
    if (state.kind !== "ready") return;
    await copyToClipboard(state.url);
    setState({ ...state, copied: true });
    toast.info("link copied", { description: state.url, durationMs: 2500 });
    setTimeout(() => {
      setState((s) => (s.kind === "ready" ? { ...s, copied: false } : s));
    }, 1400);
  }

  if (state.kind === "ready") {
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={copyAgain}
          className={`flex items-center gap-1.5 border border-[var(--color-line)] bg-[var(--color-panel-2)] ${compact ? "px-1.5 py-1" : "px-2.5 py-1"} font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-panel)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-phosphor)]`}
          title={state.copied ? `copied ${state.url}` : `copy ${state.url}`}
          aria-label={state.copied ? "link copied" : "copy share link"}
        >
          {state.copied ? (
            <Check size={12} weight="duotone" />
          ) : (
            <Copy size={12} weight="duotone" />
          )}
          {!compact && <span>{state.copied ? "copied" : "copy link"}</span>}
        </button>
        <a
          href={state.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] underline-offset-2 hover:text-[var(--color-phosphor)] hover:underline"
          title={state.url}
        >
          open
        </a>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={create}
          className="flex items-center gap-1.5 border border-[var(--color-line)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
        >
          <ShareNetwork size={12} weight="duotone" />
          retry
        </button>
        <span className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-magenta)]">
          <Warning size={12} weight="duotone" />
          {state.message.slice(0, 60)}
        </span>
      </div>
    );
  }

  const busy = state.kind === "creating";
  const disabled = busy || !payload;
  return (
    <button
      onClick={create}
      disabled={disabled}
      title={!payload ? "nothing to share" : compact ? "create public share link" : undefined}
      aria-label={compact ? (busy ? "creating share link" : "share") : undefined}
      className={`flex items-center gap-1.5 border border-[var(--color-line)] ${compact ? "px-1.5 py-1" : "bg-[var(--color-panel-2)] px-2.5 py-1"} font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:bg-[var(--color-panel)] disabled:opacity-50 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-phosphor)]`}
    >
      <ShareNetwork size={13} weight="duotone" />
      {!compact && <span>{busy ? "creating..." : "share"}</span>}
    </button>
  );
}

async function copyToClipboard(text: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // fall through to fallback
    }
  }
  if (typeof document !== "undefined") {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } finally {
      document.body.removeChild(ta);
    }
  }
}
