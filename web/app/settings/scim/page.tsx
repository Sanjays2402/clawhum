"use client";

/**
 * SCIM 2.0 token administration.
 *
 * One screen per workspace: shows whether a SCIM bearer is configured,
 * lets an admin mint or revoke it, and surfaces the SCIM base URL plus
 * one-time bearer so it can be pasted into Okta / Azure AD / Google
 * Workspace. The plaintext token is only ever returned by the mint
 * call so we keep it in component state and warn the operator before
 * navigating away.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  CloudArrowUp,
  ArrowLeft,
  Copy,
  Trash,
  Warning,
  Plus,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface TokenStatus {
  configured: boolean;
  tenant_id?: string;
  created_by?: string;
  created_at?: number;
  last_used_at?: number;
  revoked?: boolean;
}

interface MintResponse extends TokenStatus {
  token: string;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: TokenStatus }
  | { kind: "error"; status: number; message: string };

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const k = getApiKey();
  const base: Record<string, string> = k ? { "X-API-Key": k } : {};
  return { ...base, ...(extra || {}) };
}

function fmtTs(ts?: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function scimBaseUrl(): string {
  if (typeof window === "undefined") return "/scim/v2";
  // The API listens on the same origin as the dashboard during local
  // development, and behind the same hostname in production. Surface
  // the canonical mount point so admins paste the right value.
  const u = new URL(window.location.href);
  // If a dedicated API host is set via env, callers can edit the
  // displayed URL after copying. We always show the relative path so
  // both single host and split host deployments work.
  return `${u.protocol}//${u.host}/scim/v2`;
}

export default function ScimSettingsPage() {
  useApiKey(); // re-render on key change
  const [state, setState] = useState<State>({ kind: "loading" });
  const [minted, setMinted] = useState<string | null>(null);
  const [busy, setBusy] = useState<"mint" | "revoke" | null>(null);
  const [mfa, setMfa] = useState("");
  const [copied, setCopied] = useState<"token" | "url" | null>(null);

  const load = async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/admin/scim/token", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const txt = await r.text();
        setState({ kind: "error", status: r.status, message: txt });
        return;
      }
      const data = (await r.json()) as TokenStatus;
      setState({ kind: "ready", data });
    } catch (e) {
      setState({ kind: "error", status: 0, message: String(e) });
    }
  };

  useEffect(() => {
    load();
  }, []);

  const mint = async () => {
    setBusy("mint");
    try {
      const r = await fetch("/api/admin/scim/token", {
        method: "POST",
        headers: authHeaders(mfa ? { "X-MFA-Code": mfa } : undefined),
      });
      if (!r.ok) {
        const txt = await r.text();
        alert(`Mint failed (${r.status}): ${txt}`);
        return;
      }
      const data = (await r.json()) as MintResponse;
      setMinted(data.token);
      setMfa("");
      await load();
    } finally {
      setBusy(null);
    }
  };

  const revoke = async () => {
    if (!confirm("Revoke the current SCIM token? Your IdP sync will stop.")) {
      return;
    }
    setBusy("revoke");
    try {
      const r = await fetch("/api/admin/scim/token", {
        method: "DELETE",
        headers: authHeaders(mfa ? { "X-MFA-Code": mfa } : undefined),
      });
      if (!r.ok && r.status !== 204) {
        const txt = await r.text();
        alert(`Revoke failed (${r.status}): ${txt}`);
        return;
      }
      setMinted(null);
      await load();
    } finally {
      setBusy(null);
    }
  };

  const copy = async (text: string, which: "token" | "url") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(which);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // Ignored: best-effort copy.
    }
  };

  return (
    <div className="px-4 md:px-8 py-6 max-w-[900px] mx-auto space-y-6">
      <header className="flex flex-col gap-2">
        <Link
          href="/admin"
          className="text-[11px] font-mono uppercase tracking-widest text-[var(--color-dim)] inline-flex items-center gap-1 hover:text-[var(--color-fg)] w-fit"
        >
          <ArrowLeft size={12} weight="duotone" /> admin
        </Link>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <CloudArrowUp size={20} weight="duotone" /> scim provisioning
        </h1>
        <p className="text-sm text-[var(--color-dim)] max-w-prose">
          SCIM 2.0 lets your identity provider (Okta, Azure AD, Google
          Workspace) push joiners and leavers into this workspace. Mint
          one bearer token, paste it into the IdP, and member lifecycle
          stays in sync without manual tickets.
        </p>
      </header>

      <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] p-4 space-y-3">
        <h2 className="text-sm font-medium flex items-center gap-2">
          <LockKey size={14} weight="duotone" /> endpoint
        </h2>
        <div className="flex items-center gap-2 text-xs font-mono">
          <code className="flex-1 px-2 py-1 rounded bg-[var(--color-bg-elev)] border border-[var(--color-line)] truncate">
            {scimBaseUrl()}
          </code>
          <button
            type="button"
            onClick={() => copy(scimBaseUrl(), "url")}
            className="px-2 py-1 rounded border border-[var(--color-line)] hover:bg-[var(--color-bg-elev)] inline-flex items-center gap-1"
          >
            <Copy size={12} weight="duotone" />
            {copied === "url" ? "copied" : "copy"}
          </button>
        </div>
        <p className="text-[11px] text-[var(--color-dim)]">
          Configure your IdP with this base URL and the bearer token
          below. Discovery endpoints (<code>/ServiceProviderConfig</code>,{" "}
          <code>/Schemas</code>, <code>/ResourceTypes</code>) are served
          from the same prefix.
        </p>
      </section>

      <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] p-4 space-y-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium">bearer token</h2>
          {state.kind === "ready" && state.data.configured ? (
            <span className="text-[10px] font-mono uppercase tracking-widest text-emerald-400">
              active
            </span>
          ) : state.kind === "ready" ? (
            <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
              not configured
            </span>
          ) : null}
        </div>

        {state.kind === "loading" ? (
          <p className="text-xs text-[var(--color-dim)]">loading...</p>
        ) : state.kind === "error" ? (
          <div className="text-xs text-amber-300 flex items-start gap-2">
            <Warning size={14} weight="duotone" />
            <span>
              {state.status === 401
                ? "Sign in with an admin API key to manage SCIM."
                : state.status === 403
                ? "Your API key lacks the admin role."
                : `Error ${state.status}: ${state.message}`}
            </span>
          </div>
        ) : state.data.configured ? (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs font-mono">
            <dt className="text-[var(--color-dim)]">created by</dt>
            <dd>{state.data.created_by}</dd>
            <dt className="text-[var(--color-dim)]">created</dt>
            <dd>{fmtTs(state.data.created_at)}</dd>
            <dt className="text-[var(--color-dim)]">last used</dt>
            <dd>{fmtTs(state.data.last_used_at)}</dd>
          </dl>
        ) : (
          <p className="text-xs text-[var(--color-dim)]">
            No SCIM token exists for this workspace yet. Mint one to
            enable identity provider sync.
          </p>
        )}

        {minted ? (
          <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 space-y-2">
            <p className="text-[11px] font-mono uppercase tracking-widest text-emerald-300">
              copy now, this will not be shown again
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-2 py-1 rounded bg-[var(--color-bg-elev)] border border-[var(--color-line)] truncate font-mono text-xs">
                {minted}
              </code>
              <button
                type="button"
                onClick={() => copy(minted, "token")}
                className="px-2 py-1 rounded border border-emerald-500/40 hover:bg-emerald-500/10 inline-flex items-center gap-1 text-xs"
              >
                <Copy size={12} weight="duotone" />
                {copied === "token" ? "copied" : "copy"}
              </button>
            </div>
          </div>
        ) : null}

        <div className="flex flex-col sm:flex-row gap-2 pt-2 border-t border-[var(--color-line)]">
          <input
            type="text"
            inputMode="numeric"
            placeholder="MFA code (if enrolled)"
            value={mfa}
            onChange={(e) => setMfa(e.target.value.trim())}
            className="px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg-elev)] text-xs font-mono w-full sm:w-48"
          />
          <button
            type="button"
            onClick={mint}
            disabled={busy !== null}
            className="px-3 py-1 rounded border border-[var(--color-line)] hover:bg-[var(--color-bg-elev)] inline-flex items-center gap-1 text-xs disabled:opacity-50"
          >
            <Plus size={12} weight="duotone" />
            {state.kind === "ready" && state.data.configured
              ? "rotate token"
              : "mint token"}
          </button>
          {state.kind === "ready" && state.data.configured ? (
            <button
              type="button"
              onClick={revoke}
              disabled={busy !== null}
              className="px-3 py-1 rounded border border-rose-500/40 text-rose-300 hover:bg-rose-500/10 inline-flex items-center gap-1 text-xs disabled:opacity-50"
            >
              <Trash size={12} weight="duotone" /> revoke
            </button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
