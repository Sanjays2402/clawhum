"use client";

import { useState } from "react";
import { ShareNetwork, Check, Copy, Warning } from "@phosphor-icons/react/dist/ssr";
import type { StoredMatch } from "@/lib/history";

type State =
  | { kind: "idle" }
  | { kind: "creating" }
  | { kind: "ready"; url: string; copied: boolean }
  | { kind: "error"; message: string };

interface Props {
  match: StoredMatch;
}

export default function ShareButton({ match }: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });

  async function create() {
    setState({ kind: "creating" });
    try {
      const r = await fetch("/api/share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query_id: match.query_id,
          elapsed_ms: match.elapsed_ms,
          count: match.count,
          results: match.results,
          filename: match.filename ?? null,
          duration_sec: match.duration_sec ?? null,
        }),
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
    } catch (e: any) {
      setState({ kind: "error", message: e?.message || String(e) });
    }
  }

  async function copyAgain() {
    if (state.kind !== "ready") return;
    await copyToClipboard(state.url);
    setState({ ...state, copied: true });
    setTimeout(() => {
      setState((s) => (s.kind === "ready" ? { ...s, copied: false } : s));
    }, 1400);
  }

  if (state.kind === "ready") {
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={copyAgain}
          className="flex items-center gap-1.5 border border-[var(--color-line)] bg-[var(--color-panel-2)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-panel)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-phosphor)]"
          title={state.url}
        >
          {state.copied ? (
            <Check size={12} weight="duotone" />
          ) : (
            <Copy size={12} weight="duotone" />
          )}
          <span>{state.copied ? "copied" : "copy link"}</span>
        </button>
        <a
          href={state.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] underline-offset-2 hover:text-[var(--color-phosphor)] hover:underline"
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
  return (
    <button
      onClick={create}
      disabled={busy}
      className="flex items-center gap-1.5 border border-[var(--color-line)] bg-[var(--color-panel-2)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-50 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-phosphor)]"
    >
      <ShareNetwork size={12} weight="duotone" />
      <span>{busy ? "creating..." : "share"}</span>
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
