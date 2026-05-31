"use client";

/**
 * Workspace single sign on configuration.
 *
 * Admins point their OIDC provider (Okta, Microsoft Entra ID, Google
 * Workspace, Auth0, Keycloak, or any generic OIDC) at this workspace,
 * pick the email domain that maps to it, and toggle whether SSO is
 * enforced. When enforced is on, /me reports sso_enforced=true so the
 * sign in screen can hide the password and magic link paths.
 *
 * The client secret is never returned in plaintext; the API masks it
 * on read and the UI shows the masked form by default. To rotate the
 * secret, type the new one in and save; leaving the field blank on an
 * update keeps the existing secret.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  Buildings,
  LinkSimple,
  Check,
  Copy,
  Trash,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface Provider {
  id: string;
  label: string;
}

interface ProvidersResp {
  providers: Provider[];
  default_redirect_uri: string;
}

interface SSOConfig {
  provider: string;
  provider_label: string;
  issuer: string;
  client_id: string;
  client_secret: string; // masked
  email_domain: string;
  enforced: boolean;
  auto_join: boolean;
  auto_join_role: string;
  created_at: number;
  updated_at: number;
  created_by: string;
  discovery_url: string;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; config: SSOConfig | null; providers: Provider[]; redirect: string }
  | { kind: "error"; status: number; message: string };

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const k = getApiKey();
  const base: Record<string, string> = k ? { "X-API-Key": k } : {};
  return { ...base, ...(extra || {}) };
}

function formatTs(ts: number): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

export default function SSOSettingsPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [provider, setProvider] = useState("okta");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [emailDomain, setEmailDomain] = useState("");
  const [enforced, setEnforced] = useState(false);
  const [autoJoin, setAutoJoin] = useState(false);
  const [autoJoinRole, setAutoJoinRole] = useState<"admin" | "writer" | "reader">("reader");
  const [mfaCode, setMfaCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const [pRes, cRes] = await Promise.all([
        fetch("/api/sso/providers"),
        fetch("/api/sso/config", { headers: authHeaders() }),
      ]);
      if (!pRes.ok) {
        setState({ kind: "error", status: pRes.status, message: await pRes.text() });
        return;
      }
      const providersResp = (await pRes.json()) as ProvidersResp;
      let config: SSOConfig | null = null;
      if (cRes.ok) {
        const body = await cRes.text();
        config = body ? (JSON.parse(body) as SSOConfig | null) : null;
      } else if (cRes.status !== 404) {
        setState({ kind: "error", status: cRes.status, message: await cRes.text() });
        return;
      }
      setState({
        kind: "ready",
        config,
        providers: providersResp.providers,
        redirect: providersResp.default_redirect_uri,
      });
      if (config) {
        setProvider(config.provider);
        setIssuer(config.issuer);
        setClientId(config.client_id);
        setEmailDomain(config.email_domain);
        setEnforced(config.enforced);
        setAutoJoin(config.auto_join);
        setAutoJoinRole(
          (config.auto_join_role === "admin" || config.auto_join_role === "writer")
            ? config.auto_join_role
            : "reader"
        );
        setClientSecret("");
      }
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

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!issuer.trim() || !clientId.trim() || !emailDomain.trim()) {
      setFormError("provider, issuer, client id, and email domain are all required");
      return;
    }
    setSaving(true);
    try {
      const headers = authHeaders({ "Content-Type": "application/json" });
      if (mfaCode.trim()) headers["X-MFA-Code"] = mfaCode.trim();
      const r = await fetch("/api/sso/config", {
        method: "PUT",
        headers,
        body: JSON.stringify({
          provider,
          issuer: issuer.trim(),
          client_id: clientId.trim(),
          client_secret: clientSecret,
          email_domain: emailDomain.trim().toLowerCase(),
          enforced,
          auto_join: autoJoin,
          auto_join_role: autoJoinRole,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        if (r.status === 401 && r.headers.get("www-authenticate") === "MFA") {
          setFormError("admin MFA code required. enrol at settings then enter the 6 digit code.");
        } else {
          setFormError(body.detail || `save failed (${r.status})`);
        }
        return;
      }
      setClientSecret("");
      setMfaCode("");
      await refresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete() {
    setDeleting(true);
    try {
      const headers = authHeaders();
      if (mfaCode.trim()) headers["X-MFA-Code"] = mfaCode.trim();
      const r = await fetch("/api/sso/config", { method: "DELETE", headers });
      if (!r.ok && r.status !== 204) {
        const body = await r.json().catch(() => ({}));
        if (r.status === 401 && r.headers.get("www-authenticate") === "MFA") {
          setFormError("admin MFA code required to remove the SSO config");
        } else {
          setFormError(body.detail || `delete failed (${r.status})`);
        }
        return;
      }
      setConfirmDelete(false);
      setIssuer("");
      setClientId("");
      setClientSecret("");
      setEmailDomain("");
      setEnforced(false);
      setAutoJoin(false);
      setAutoJoinRole("reader");
      await refresh();
    } finally {
      setDeleting(false);
    }
  }

  async function copyRedirect(uri: string) {
    try {
      await navigator.clipboard.writeText(uri);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <Link
          href="/settings"
          className="inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
        >
          <ArrowLeft size={14} weight="duotone" /> back to settings
        </Link>
      </div>

      <header className="mb-6 flex items-center gap-3">
        <ShieldCheck size={22} weight="duotone" className="text-[var(--color-phosphor)]" />
        <h1 className="font-mono text-lg uppercase tracking-widest">single sign on</h1>
      </header>

      {state.kind === "loading" && (
        <div className="panel rounded-[2px] p-6 space-y-3" aria-busy="true">
          <div className="h-4 w-2/3 animate-pulse bg-[var(--color-line)]/40" />
          <div className="h-4 w-1/2 animate-pulse bg-[var(--color-line)]/40" />
          <div className="h-4 w-3/4 animate-pulse bg-[var(--color-line)]/40" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="panel rounded-[2px] p-6 space-y-2">
          <div className="flex items-center gap-2 text-[var(--color-warn)]">
            <Warning size={16} weight="duotone" />
            <span className="font-mono text-xs uppercase tracking-widest">
              failed to load (status {state.status})
            </span>
          </div>
          <pre className="overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-[var(--color-dim)]">
            {state.message || "no detail"}
          </pre>
          <button
            onClick={refresh}
            className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest hover:text-[var(--color-phosphor)]"
          >
            retry
          </button>
        </div>
      )}

      {state.kind === "ready" && (
        <div className="space-y-6">
          <section className="panel rounded-[2px] p-4 space-y-2">
            <div className="flex items-center gap-2">
              <Buildings size={14} weight="duotone" className="text-[var(--color-muted)]" />
              <span className="label-xs">current status</span>
            </div>
            {state.config ? (
              <div className="space-y-1 font-mono text-[11px] text-[var(--color-dim)]">
                <div>
                  provider: <span className="text-[var(--color-phosphor)]">{state.config.provider_label}</span>
                </div>
                <div>email domain: {state.config.email_domain}</div>
                <div>
                  enforce sign in:{" "}
                  <span
                    className={
                      state.config.enforced ? "text-[var(--color-phosphor)]" : "text-[var(--color-warn)]"
                    }
                  >
                    {state.config.enforced ? "on" : "off"}
                  </span>
                </div>
                <div>
                  domain auto-join:{" "}
                  <span
                    className={
                      state.config.auto_join ? "text-[var(--color-phosphor)]" : "text-[var(--color-muted)]"
                    }
                  >
                    {state.config.auto_join ? `on (role: ${state.config.auto_join_role})` : "off"}
                  </span>
                </div>
                <div>updated: {formatTs(state.config.updated_at)} by {state.config.created_by || "unknown"}</div>
                <div className="flex items-center gap-2 break-all">
                  <LinkSimple size={12} weight="duotone" /> {state.config.discovery_url}
                </div>
              </div>
            ) : (
              <p className="font-mono text-[11px] text-[var(--color-dim)]">
                no SSO provider configured. fill out the form below to wire one up.
              </p>
            )}
          </section>

          <section className="panel rounded-[2px] p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="label-xs">redirect URI</span>
              <button
                type="button"
                onClick={() => copyRedirect(state.redirect)}
                className="inline-flex items-center gap-1 border border-[var(--color-line)] px-2 py-1 font-mono text-[10px] uppercase tracking-widest hover:text-[var(--color-phosphor)]"
              >
                {copied ? <Check size={12} weight="duotone" /> : <Copy size={12} weight="duotone" />}
                {copied ? "copied" : "copy"}
              </button>
            </div>
            <code className="block break-all rounded-[2px] border border-[var(--color-line)] bg-[var(--color-bg)]/40 px-2 py-1 font-mono text-[11px] text-[var(--color-dim)]">
              {state.redirect}
            </code>
            <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
              paste this into your IdP as an allowed redirect URI. it points to the API host that completes the OIDC code exchange.
            </p>
          </section>

          <form onSubmit={onSubmit} className="panel rounded-[2px] p-4 space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block space-y-1">
                <span className="label-xs">provider</span>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="w-full border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px]"
                >
                  {state.providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-1">
                <span className="label-xs">email domain</span>
                <input
                  value={emailDomain}
                  onChange={(e) => setEmailDomain(e.target.value)}
                  placeholder="acme.com"
                  autoCapitalize="off"
                  autoCorrect="off"
                  className="w-full border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px]"
                />
              </label>
            </div>

            <label className="block space-y-1">
              <span className="label-xs">issuer (OIDC discovery base URL)</span>
              <input
                value={issuer}
                onChange={(e) => setIssuer(e.target.value)}
                placeholder="https://acme.okta.com"
                autoCapitalize="off"
                autoCorrect="off"
                className="w-full border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px]"
              />
            </label>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block space-y-1">
                <span className="label-xs">client id</span>
                <input
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder="0oa1abc2def3"
                  autoCapitalize="off"
                  autoCorrect="off"
                  className="w-full border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px]"
                />
              </label>
              <label className="block space-y-1">
                <span className="label-xs">
                  client secret {state.config ? "(leave blank to keep current)" : ""}
                </span>
                <input
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  type="password"
                  autoComplete="new-password"
                  placeholder={state.config?.client_secret || ""}
                  className="w-full border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px]"
                />
              </label>
            </div>

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={enforced}
                onChange={(e) => setEnforced(e.target.checked)}
                className="h-4 w-4 accent-[var(--color-phosphor)]"
              />
              <span className="font-mono text-[12px]">
                enforce sso for this email domain (hide password and magic link sign in)
              </span>
            </label>

            <div className="space-y-2 border border-[var(--color-line)] p-3">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={autoJoin}
                  onChange={(e) => setAutoJoin(e.target.checked)}
                  className="h-4 w-4 accent-[var(--color-phosphor)]"
                />
                <span className="font-mono text-[12px]">
                  domain auto-join: provision a seat on first sign in from this email domain
                </span>
              </label>
              <label className="block space-y-1">
                <span className="label-xs">default role for auto-joined members</span>
                <select
                  value={autoJoinRole}
                  onChange={(e) => setAutoJoinRole(e.target.value as "admin" | "writer" | "reader")}
                  disabled={!autoJoin}
                  className="w-40 border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px] disabled:opacity-50"
                >
                  <option value="reader">reader</option>
                  <option value="writer">writer</option>
                  <option value="admin">admin</option>
                </select>
                <span className="block font-mono text-[11px] text-[var(--color-muted)]">
                  pick the least-privileged role that matches your onboarding policy. you can promote later from the members page.
                </span>
              </label>
            </div>

            <label className="block space-y-1">
              <span className="label-xs">admin MFA code (if enrolled)</span>
              <input
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value)}
                placeholder="123456"
                inputMode="numeric"
                pattern="[0-9]*"
                autoComplete="one-time-code"
                className="w-40 border border-[var(--color-line)] bg-[var(--color-bg)] px-2 py-1 font-mono text-[12px]"
              />
            </label>

            {formError && (
              <div className="flex items-start gap-2 text-[var(--color-warn)]">
                <Warning size={14} weight="duotone" className="mt-[2px]" />
                <span className="font-mono text-[11px]">{formError}</span>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="border border-[var(--color-phosphor)] bg-[var(--color-phosphor)]/10 px-4 py-1 font-mono text-[12px] uppercase tracking-widest text-[var(--color-phosphor)] disabled:opacity-50"
              >
                {saving ? "saving" : state.config ? "update config" : "save config"}
              </button>
              {state.config && (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-warn)] hover:bg-[var(--color-warn)]/10"
                >
                  <Trash size={12} weight="duotone" /> remove
                </button>
              )}
            </div>
          </form>

          {confirmDelete && (
            <div
              role="dialog"
              aria-modal="true"
              className="panel rounded-[2px] border border-[var(--color-warn)]/60 p-4 space-y-3"
            >
              <p className="font-mono text-[12px] text-[var(--color-warn)]">
                remove the SSO config for this workspace? users in this email domain will fall back to password sign in.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={onDelete}
                  disabled={deleting}
                  className="border border-[var(--color-warn)] bg-[var(--color-warn)]/10 px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-warn)]"
                >
                  {deleting ? "removing" : "yes, remove"}
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
                  className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest"
                >
                  cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
