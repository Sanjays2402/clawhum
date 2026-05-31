"use client";

import { useState } from "react";
import {
  Flask,
  Play,
  Warning,
  Check,
  ArrowsClockwise,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey } from "@/lib/apiKey";

type Target =
  | { kind: "history"; id: string }
  | { kind: "collection"; id: string }
  | { kind: "history_view"; id: string }
  | { kind: "share"; id: string }
  | { kind: "webhook"; id: string }
  | { kind: "api_key"; id: string }
  | { kind: "ip_allowlist_rule"; id: string }
  | { kind: "privacy_erasure"; id: string };

const TARGETS: { label: string; kind: Target["kind"]; path: (id: string) => string; idHint: string }[] = [
  { label: "History entry", kind: "history", path: (id) => `/api/history/${id}`, idHint: "history id" },
  { label: "Collection", kind: "collection", path: (id) => `/api/collections/${id}`, idHint: "collection id" },
  { label: "Saved view", kind: "history_view", path: (id) => `/api/history/views/${id}`, idHint: "view id" },
  { label: "Share link", kind: "share", path: (id) => `/api/share/${id}`, idHint: "share id" },
  { label: "Webhook", kind: "webhook", path: (id) => `/api/webhooks/${id}`, idHint: "webhook id" },
  { label: "API key", kind: "api_key", path: (id) => `/api/keys/${id}`, idHint: "key id" },
  { label: "IP allowlist rule", kind: "ip_allowlist_rule", path: (id) => `/api/ip-allowlist/${id}`, idHint: "rule id" },
  { label: "Privacy erasure (DANGER)", kind: "privacy_erasure", path: () => `/api/v1/privacy/me`, idHint: "n/a" },
];

interface PreviewBody {
  dry_run: true;
  would_delete: { kind: string; id?: string; [k: string]: unknown };
  tenant_id?: string;
  warnings?: string[];
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; status: number; body: PreviewBody }
  | { kind: "error"; status: number; message: string };

export default function SandboxPage() {
  const [target, setTarget] = useState<Target["kind"]>("history");
  const [id, setId] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const t = TARGETS.find((x) => x.kind === target)!;
  const requiresId = target !== "privacy_erasure";

  async function run() {
    setState({ kind: "loading" });
    try {
      const headers: Record<string, string> = { "X-Dry-Run": "1" };
      const k = getApiKey();
      if (k) headers["X-API-Key"] = k;
      const url = t.path(id.trim()) + (t.path(id.trim()).includes("?") ? "&" : "?") + "dry_run=true";
      const r = await fetch(url, { method: "DELETE", headers, cache: "no-store" });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        setState({ kind: "error", status: r.status, message: text || r.statusText });
        return;
      }
      const body = (await r.json()) as PreviewBody;
      setState({ kind: "ok", status: r.status, body });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setState({ kind: "error", status: 0, message: msg });
    }
  }

  return (
    <div className="px-4 py-4 space-y-4 max-w-[1100px]">
      <div className="flex items-center justify-between">
        <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
          settings / sandbox / dry run preview
        </h1>
        <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          nothing is mutated
        </span>
      </div>

      <section className="panel rounded-[2px] p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Flask size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
          <span className="label-xs">preview a destructive call</span>
        </div>

        <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
          every DELETE endpoint accepts <code>?dry_run=true</code> or the header <code>X-Dry-Run: 1</code>. the server runs full auth, tenant, and permission checks then returns a structured preview of what would be removed. use this from CI to validate destructive scripts without touching live data.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_2fr_auto] gap-2 items-start">
          <label className="space-y-1">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">resource</span>
            <select
              aria-label="Resource to preview"
              value={target}
              onChange={(e) => { setTarget(e.target.value as Target["kind"]); setState({ kind: "idle" }); }}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
            >
              {TARGETS.map((opt) => (
                <option key={opt.kind} value={opt.kind}>{opt.label}</option>
              ))}
            </select>
          </label>

          <label className="space-y-1">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">{t.idHint}</span>
            <input
              aria-label="Resource identifier"
              value={id}
              onChange={(e) => setId(e.target.value)}
              disabled={!requiresId}
              placeholder={requiresId ? t.idHint : "no id required"}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)] disabled:opacity-50"
              autoComplete="off"
              spellCheck={false}
            />
          </label>

          <div className="space-y-1">
            <span className="font-mono text-[10px] uppercase tracking-widest text-transparent select-none">go</span>
            <button
              type="button"
              onClick={run}
              disabled={state.kind === "loading" || (requiresId && !id.trim())}
              className="border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest hover:bg-[rgba(0,255,140,0.06)] disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
            >
              {state.kind === "loading" ? <ArrowsClockwise size={12} weight="duotone" className="animate-spin" /> : <Play size={12} weight="duotone" />}
              preview
            </button>
          </div>
        </div>

        <pre className="bg-[var(--color-bg)] border border-[var(--color-line)] p-2 font-mono text-[11px] text-[var(--color-muted)] whitespace-pre-wrap break-all">
{`curl -X DELETE ${typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:7452"}${requiresId ? t.path(id.trim() || "<id>") : t.path("")}?dry_run=true \\
  -H "X-API-Key: $CLAWHUM_API_KEY"`}
        </pre>
      </section>

      <section className="panel rounded-[2px] p-4 space-y-3">
        <div className="flex items-center gap-2">
          <span className="label-xs">result</span>
          {state.kind === "ok" && (
            <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)] inline-flex items-center gap-1.5">
              <Check size={11} weight="duotone" /> http {state.status} / no mutation
            </span>
          )}
        </div>

        {state.kind === "idle" && (
          <div className="font-mono text-[11px] text-[var(--color-dim)] py-6 text-center border border-dashed border-[var(--color-line)]">
            pick a resource, paste an id, press preview.
          </div>
        )}

        {state.kind === "loading" && (
          <div className="space-y-2" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-3 bg-[var(--color-panel)] animate-pulse rounded-[2px]" />
            ))}
          </div>
        )}

        {state.kind === "error" && (
          <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] p-2 font-mono text-[11px] text-[var(--color-amber)] flex items-start gap-2">
            <Warning size={14} weight="duotone" className="mt-0.5" />
            <div>
              <div className="uppercase tracking-widest">
                {state.status === 401 ? "auth required" :
                 state.status === 403 ? "forbidden" :
                 state.status === 404 ? "not found" :
                 state.status === 0 ? "api unreachable" :
                 `error / ${state.status}`}
              </div>
              <div className="text-[10px] text-[var(--color-dim)] mt-0.5 break-words">{state.message}</div>
            </div>
          </div>
        )}

        {state.kind === "ok" && (
          <div className="space-y-2">
            {state.body.warnings && state.body.warnings.length > 0 && (
              <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] p-2 font-mono text-[11px] text-[var(--color-amber)] flex items-start gap-2">
                <Warning size={14} weight="duotone" className="mt-0.5" />
                <ul className="space-y-1">
                  {state.body.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </div>
            )}
            <pre className="bg-[var(--color-bg)] border border-[var(--color-line)] p-2 font-mono text-[11px] text-[var(--color-text)] whitespace-pre-wrap break-all">
{JSON.stringify(state.body, null, 2)}
            </pre>
          </div>
        )}
      </section>
    </div>
  );
}
