"use client";

/**
 * Personal access tokens.
 *
 * Mints, lists, and revokes tokens scoped to the caller's tenant. The
 * freshly created secret is shown exactly once; afterwards only the
 * last four characters survive in the UI. All requests reuse the
 * existing X-API-Key auth via the fetch patch in lib/apiKey.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Key,
  Plus,
  Copy,
  Check,
  Trash,
  Warning,
  ShieldCheck,
  Terminal,
  ArrowLeft,
  GlobeHemisphereWest,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface KeyRow {
  id: string;
  name: string;
  roles: string[];
  rpm: number;
  created_at: number;
  last_used_at: number;
  last_used_ip?: string;
  last_used_ua?: string;
  secret_hint: string;
  expires_at: number;
  expired: boolean;
  scopes: string[];
  effective_scopes: string[];
  prior_secret_hint?: string;
  prior_secret_expires_at?: number;
  rotation_active?: boolean;
  ip_cidrs?: string[];
}

interface CreatedKey extends KeyRow {
  secret: string;
}

interface KeyPolicy {
  max_ttl_days: number;
  default_ttl_days: number;
  available_scopes: string[];
  allowed_scopes: string[];
}

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; rows: KeyRow[] }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function timeAgo(ts: number): string {
  if (!ts) return "never";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function ttlChoices(
  policy: KeyPolicy | null,
): { value: string; label: string }[] {
  const cap = policy?.max_ttl_days ?? 0;
  const base = [
    { value: "1", label: "in 1 day" },
    { value: "7", label: "in 7 days" },
    { value: "30", label: "in 30 days" },
    { value: "90", label: "in 90 days" },
    { value: "180", label: "in 180 days" },
    { value: "365", label: "in 365 days" },
  ];
  const filtered = cap > 0 ? base.filter((o) => Number(o.value) <= cap) : base;
  if (cap > 0 && !filtered.some((o) => Number(o.value) === cap)) {
    filtered.push({ value: String(cap), label: `in ${cap} days (max)` });
  }
  if (cap === 0) filtered.push({ value: "never", label: "never" });
  return filtered;
}

function expiryLabel(expiresAt: number, expired: boolean): string {
  if (!expiresAt) return "never";
  if (expired) return "expired";
  const d = expiresAt - Date.now() / 1000;
  if (d < 3600) return `in ${Math.max(1, Math.floor(d / 60))}m`;
  if (d < 86400) return `in ${Math.floor(d / 3600)}h`;
  return `in ${Math.floor(d / 86400)}d`;
}

export default function KeysPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [ttlDays, setTtlDays] = useState<string>("90");
  const [policy, setPolicy] = useState<KeyPolicy | null>(null);
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [justCreated, setJustCreated] = useState<CreatedKey | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [rotateId, setRotateId] = useState<string | null>(null);
  const [rotateGrace, setRotateGrace] = useState<string>("30");
  const [rotating, setRotating] = useState<string | null>(null);
  const [rotateError, setRotateError] = useState<string | null>(null);
  const [revokeAllOpen, setRevokeAllOpen] = useState(false);
  const [revokingAll, setRevokingAll] = useState(false);
  const [ipId, setIpId] = useState<string | null>(null);
  const [ipDraft, setIpDraft] = useState<string>("");
  const [ipSaving, setIpSaving] = useState<string | null>(null);
  const [ipError, setIpError] = useState<string | null>(null);
  const [revokeAllError, setRevokeAllError] = useState<string | null>(null);
  const [revokeAllResult, setRevokeAllResult] = useState<
    { revoked: number; preserved: boolean } | null
  >(null);
  const [copied, setCopied] = useState<"secret" | "curl" | null>(null);
  const [origin, setOrigin] = useState("http://127.0.0.1:7452");

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/keys", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => r.statusText);
        setState({ kind: "error", status: r.status, message: msg || r.statusText });
        return;
      }
      const rows = (await r.json()) as KeyRow[];
      setState({ kind: "ready", rows });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  // Load workspace TTL policy so the dropdown reflects real limits.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/keys/policy", {
          headers: authHeaders(),
          cache: "no-store",
        });
        if (!r.ok || !alive) return;
        const p = (await r.json()) as KeyPolicy;
        if (!alive) return;
        setPolicy(p);
        if (p.default_ttl_days > 0) setTtlDays(String(p.default_ttl_days));
        else if (p.max_ttl_days > 0) setTtlDays(String(p.max_ttl_days));
      } catch {
        /* policy is advisory; mint still works */
      }
    })();
    return () => {
      alive = false;
    };
  }, [storedKey]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const r = await fetch("/api/keys", {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          expires_in_days: ttlDays === "never" ? 0 : Number(ttlDays),
          scopes: Array.from(selectedScopes),
        }),
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => r.statusText);
        setCreateError(msg || `failed with ${r.status}`);
        return;
      }
      const created = (await r.json()) as CreatedKey;
      setJustCreated(created);
      setName("");
      setSelectedScopes(new Set());
      await refresh();
    } catch (e: any) {
      setCreateError(e?.message || String(e));
    } finally {
      setCreating(false);
    }
  }

  async function rotate(id: string) {
    if (rotating) return;
    setRotating(id);
    setRotateError(null);
    try {
      const grace =
        rotateGrace === "now" ? 0 : Math.max(0, parseInt(rotateGrace, 10) || 0);
      const r = await fetch(`/api/keys/${id}/rotate`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ grace_minutes: grace }),
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => "");
        setRotateError(msg || `failed with ${r.status}`);
        return;
      }
      const body = (await r.json()) as CreatedKey;
      setJustCreated(body);
      setRotateId(null);
      await refresh();
    } catch (e: any) {
      setRotateError(e?.message || String(e));
    } finally {
      setRotating(null);
    }
  }

  async function revoke(id: string) {
    setRevoking(id);
    try {
      const r = await fetch(`/api/keys/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (r.ok) {
        setConfirmId(null);
        await refresh();
      }
    } finally {
      setRevoking(null);
    }
  }

  async function saveIpAllowlist(id: string) {
    if (ipSaving) return;
    setIpSaving(id);
    setIpError(null);
    // Split on commas, whitespace, or newlines so paste from a wiki
    // or a CI yaml file works without manual cleanup.
    const cidrs = ipDraft
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const r = await fetch(`/api/keys/${id}/ip-allowlist`, {
        method: "PUT",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ cidrs }),
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => "");
        setIpError(msg || `failed with ${r.status}`);
        return;
      }
      setIpId(null);
      setIpDraft("");
      await refresh();
    } catch (e: any) {
      setIpError(e?.message || String(e));
    } finally {
      setIpSaving(null);
    }
  }

  async function revokeAll() {
    if (revokingAll) return;
    setRevokingAll(true);
    setRevokeAllError(null);
    try {
      const r = await fetch("/api/keys/revoke-all", {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ include_self: false }),
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => r.statusText);
        setRevokeAllError(msg || `failed with ${r.status}`);
        return;
      }
      const body = (await r.json()) as {
        revoked: string[];
        preserved: string | null;
      };
      setRevokeAllResult({
        revoked: body.revoked.length,
        preserved: Boolean(body.preserved),
      });
      setRevokeAllOpen(false);
      await refresh();
    } catch (e: any) {
      setRevokeAllError(e?.message || String(e));
    } finally {
      setRevokingAll(false);
    }
  }

  async function copy(label: "secret" | "curl", text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      /* clipboard may be blocked; silent */
    }
  }

  const curl = justCreated
    ? `curl -X POST ${origin}/api/v1/match \\\n  -H "X-API-Key: ${justCreated.secret}" \\\n  -F "audio=@hum.wav"`
    : "";

  return (
    <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8 sm:py-12 space-y-8">
      <header className="space-y-2">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 text-xs font-mono uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft size={12} weight="bold" /> settings
        </Link>
        <div className="flex items-center gap-3">
          <Key size={28} weight="duotone" className="text-[var(--color-accent)]" />
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">
            Personal access tokens
          </h1>
        </div>
        <p className="text-sm text-[var(--color-muted)] max-w-2xl">
          Mint a token to call the ClawHum API from a script, CI job, or third
          party tool. Each token is scoped to your tenant and can be revoked at
          any time. The secret is shown exactly once.
        </p>
      </header>

      {/* Just-created banner */}
      {justCreated && (
        <section
          role="alert"
          className="rounded-lg border border-[var(--color-accent)] bg-[color-mix(in_srgb,var(--color-accent)_8%,var(--color-bg))] p-4 sm:p-5 space-y-3"
        >
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck size={18} weight="duotone" />
            Copy this token now. You will not see it again.
          </div>
          <div className="flex items-center gap-2 bg-[var(--color-bg)] border border-[var(--color-line)] rounded p-2 font-mono text-xs overflow-x-auto">
            <span className="select-all break-all">{justCreated.secret}</span>
            <button
              onClick={() => copy("secret", justCreated.secret)}
              className="ml-auto shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded hover:bg-[var(--color-panel)]"
              aria-label="Copy token"
            >
              {copied === "secret" ? (
                <>
                  <Check size={14} weight="bold" /> copied
                </>
              ) : (
                <>
                  <Copy size={14} weight="bold" /> copy
                </>
              )}
            </button>
          </div>
          <details className="text-xs text-[var(--color-muted)]">
            <summary className="cursor-pointer flex items-center gap-1 select-none">
              <Terminal size={12} weight="bold" /> sample curl
            </summary>
            <div className="mt-2 flex items-start gap-2 bg-[var(--color-bg)] border border-[var(--color-line)] rounded p-2 font-mono text-[11px] overflow-x-auto">
              <pre className="whitespace-pre">{curl}</pre>
              <button
                onClick={() => copy("curl", curl)}
                className="ml-auto shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded hover:bg-[var(--color-panel)]"
                aria-label="Copy curl"
              >
                {copied === "curl" ? (
                  <Check size={14} weight="bold" />
                ) : (
                  <Copy size={14} weight="bold" />
                )}
              </button>
            </div>
          </details>
          <button
            onClick={() => setJustCreated(null)}
            className="text-xs underline text-[var(--color-muted)] hover:text-[var(--color-text)]"
          >
            I have saved it, dismiss
          </button>
        </section>
      )}

      {/* Create form */}
      <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-4 sm:p-5">
        <h2 className="text-sm font-medium mb-3">Create a token</h2>
        <form onSubmit={create} className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="ci-bot, local-laptop, zapier..."
            maxLength={64}
            className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)]"
            aria-label="Token name"
          />
          <select
            value={ttlDays}
            onChange={(e) => setTtlDays(e.target.value)}
            aria-label="Token lifetime"
            className="bg-[var(--color-bg)] border border-[var(--color-line)] rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)]"
          >
            {ttlChoices(policy).map((opt) => (
              <option key={opt.value} value={opt.value}>
                expires {opt.label}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={!name.trim() || creating}
            className="inline-flex items-center justify-center gap-1 px-4 py-2 rounded bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90"
          >
            <Plus size={14} weight="bold" />
            {creating ? "creating..." : "create"}
          </button>
        </form>
        {policy && policy.max_ttl_days > 0 && (
          <p className="mt-2 text-[11px] text-[var(--color-muted)] font-mono uppercase tracking-wider">
            workspace cap: {policy.max_ttl_days}d
          </p>
        )}
        {policy && policy.allowed_scopes.length > 0 && (
          <fieldset className="mt-4 space-y-2">
            <legend className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-muted)]">
              scopes (leave empty for role default)
            </legend>
            <div className="flex flex-wrap gap-2">
              {policy.allowed_scopes.map((s) => {
                const checked = selectedScopes.has(s);
                return (
                  <label
                    key={s}
                    className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-[11px] font-mono cursor-pointer select-none ${
                      checked
                        ? "border-[var(--color-accent)] bg-[color-mix(in_srgb,var(--color-accent)_10%,var(--color-bg))]"
                        : "border-[var(--color-line)] hover:bg-[var(--color-bg)]"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={checked}
                      onChange={(e) => {
                        setSelectedScopes((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(s);
                          else next.delete(s);
                          return next;
                        });
                      }}
                    />
                    <span
                      aria-hidden
                      className={`inline-block h-3 w-3 rounded-sm border ${
                        checked
                          ? "bg-[var(--color-accent)] border-[var(--color-accent)]"
                          : "border-[var(--color-line)]"
                      }`}
                    />
                    {s}
                  </label>
                );
              })}
            </div>
            {selectedScopes.size === 0 && (
              <p className="text-[11px] text-[var(--color-muted)]">
                No scopes selected. Token will get every scope its role permits.
              </p>
            )}
          </fieldset>
        )}
        {createError && (
          <p className="mt-2 text-xs text-red-500 flex items-center gap-1">
            <Warning size={12} weight="bold" /> {createError}
          </p>
        )}
      </section>

      {/* Token list */}
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium">Your tokens</h2>
          {state.kind === "ready" && state.rows.length > 0 && (
            <button
              onClick={() => {
                setRevokeAllOpen(true);
                setRevokeAllError(null);
              }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded border border-red-500/50 text-red-500 text-[11px] font-mono uppercase tracking-wider hover:bg-red-500/10"
              aria-label="Revoke all other tokens"
            >
              <Warning size={12} weight="bold" /> revoke all
            </button>
          )}
        </div>

        {revokeAllResult && (
          <div
            role="status"
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-3 text-xs flex items-center gap-2"
          >
            <ShieldCheck size={14} weight="duotone" />
            Revoked {revokeAllResult.revoked} token
            {revokeAllResult.revoked === 1 ? "" : "s"}.{" "}
            {revokeAllResult.preserved
              ? "Your current token was preserved so you stay signed in."
              : "No tokens were preserved."}
          </div>
        )}

        {revokeAllOpen && (
          <div
            role="alertdialog"
            aria-labelledby="revoke-all-title"
            className="rounded-lg border border-red-500/50 bg-red-500/5 p-4 space-y-3"
          >
            <div id="revoke-all-title" className="flex items-center gap-2 text-sm font-medium">
              <Warning size={16} weight="duotone" /> Revoke every token in this workspace?
            </div>
            <p className="text-xs text-[var(--color-muted)]">
              Use this if a token has leaked. Every personal access token in
              this workspace will stop working immediately. The token you are
              currently signed in with is preserved so you can mint fresh
              credentials. This action is audited and requires MFA when
              enabled.
            </p>
            {revokeAllError && (
              <p className="text-xs text-red-500 flex items-center gap-1">
                <Warning size={12} weight="bold" /> {revokeAllError}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={revokeAll}
                disabled={revokingAll}
                className="px-3 py-1.5 rounded bg-red-500 text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
              >
                {revokingAll ? "revoking..." : "yes, revoke them all"}
              </button>
              <button
                onClick={() => setRevokeAllOpen(false)}
                disabled={revokingAll}
                className="px-3 py-1.5 rounded border border-[var(--color-line)] text-xs"
              >
                cancel
              </button>
            </div>
          </div>
        )}

        {state.kind === "loading" && (
          <div className="space-y-2" aria-busy="true">
            {[0, 1].map((i) => (
              <div
                key={i}
                className="h-16 rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] animate-pulse"
              />
            ))}
          </div>
        )}

        {state.kind === "error" && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium">
              <Warning size={16} weight="duotone" />
              Could not load tokens
            </div>
            <p className="mt-1 text-[var(--color-muted)] text-xs">
              {state.status === 401
                ? "Set your API key in settings first."
                : state.status === 403
                  ? "Your current key cannot manage tokens. You need the writer role."
                  : state.message}
            </p>
            {state.status === 401 && (
              <Link
                href="/settings"
                className="mt-2 inline-block text-xs underline"
              >
                go to settings
              </Link>
            )}
          </div>
        )}

        {state.kind === "ready" && state.rows.length === 0 && (
          <div className="rounded-lg border border-dashed border-[var(--color-line)] p-8 text-center text-sm text-[var(--color-muted)]">
            <Key
              size={32}
              weight="duotone"
              className="mx-auto mb-2 text-[var(--color-muted)]"
            />
            No tokens yet. Mint one above to call the API from outside the
            browser.
          </div>
        )}

        {state.kind === "ready" &&
          state.rows.length > 0 &&
          state.rows
            .slice()
            .sort((a, b) => b.created_at - a.created_at)
            .map((row) => (
              <div
                key={row.id}
                className="rounded-lg border border-[var(--color-line)] bg-[var(--color-panel)] p-4 flex flex-col sm:flex-row sm:items-center gap-3"
              >
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sm truncate">{row.name}</span>
                    <span className="font-mono text-[11px] text-[var(--color-muted)]">
                      pat_...{row.secret_hint}
                    </span>
                    {row.expired && (
                      <span
                        className="inline-flex items-center gap-1 rounded border border-red-500/50 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wider text-red-500"
                        title="This token has expired and can no longer authenticate"
                      >
                        <Warning size={10} weight="bold" /> expired
                      </span>
                    )}
                    {row.rotation_active && (
                      <span
                        className="inline-flex items-center gap-1 rounded border border-amber-500/50 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wider text-amber-500"
                        title={`Previous secret pat_...${row.prior_secret_hint} still valid until ${row.prior_secret_expires_at ? new Date(row.prior_secret_expires_at * 1000).toLocaleString() : ""}`}
                      >
                        <ShieldCheck size={10} weight="bold" /> rotating
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--color-muted)] font-mono uppercase tracking-wider">
                    <span>roles: {row.roles.join(", ") || "reader"}</span>
                    <span title={row.effective_scopes.join(", ")}>
                      scopes: {row.scopes.length > 0 ? row.scopes.join(", ") : "role default"}
                    </span>
                    <span
                      title={
                        row.last_used_at > 0
                          ? `Last successful auth from ${row.last_used_ip || "unknown ip"}${row.last_used_ua ? " using " + row.last_used_ua : ""}`
                          : "Token has not been used since it was minted."
                      }
                    >
                      used: {timeAgo(row.last_used_at)}
                      {row.last_used_at > 0 && row.last_used_ip ? (
                        <span className="ml-1 normal-case tracking-normal text-[var(--color-muted)]/70">
                          from {row.last_used_ip}
                        </span>
                      ) : null}
                    </span>
                    <span>created: {timeAgo(row.created_at)}</span>
                    <span
                      className={
                        row.expired ? "text-red-500" : undefined
                      }
                    >
                      expires: {expiryLabel(row.expires_at, row.expired)}
                    </span>
                    <span
                      title={
                        (row.ip_cidrs?.length ?? 0) > 0
                          ? `Token rejected from any IP outside: ${row.ip_cidrs!.join(", ")}`
                          : "Token usable from any IP. Click 'ip allowlist' to restrict."
                      }
                      className={
                        (row.ip_cidrs?.length ?? 0) > 0
                          ? "text-[var(--color-accent)]"
                          : undefined
                      }
                    >
                      ips:{" "}
                      {(row.ip_cidrs?.length ?? 0) > 0
                        ? row.ip_cidrs!.length === 1
                          ? row.ip_cidrs![0]
                          : `${row.ip_cidrs!.length} ranges`
                        : "any"}
                    </span>
                  </div>
                </div>
                {confirmId === row.id ? (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => revoke(row.id)}
                      disabled={revoking === row.id}
                      className="px-3 py-1.5 rounded bg-red-500 text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
                    >
                      {revoking === row.id ? "revoking..." : "confirm revoke"}
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      className="px-3 py-1.5 rounded border border-[var(--color-line)] text-xs"
                    >
                      cancel
                    </button>
                  </div>
                ) : rotateId === row.id ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="text-[11px] text-[var(--color-muted)] font-mono uppercase tracking-wider">
                      grace
                    </label>
                    <select
                      value={rotateGrace}
                      onChange={(e) => setRotateGrace(e.target.value)}
                      className="px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-xs"
                      aria-label="Rotation grace window"
                    >
                      <option value="now">revoke old now</option>
                      <option value="5">5 minutes</option>
                      <option value="15">15 minutes</option>
                      <option value="30">30 minutes</option>
                      <option value="60">60 minutes</option>
                    </select>
                    <button
                      onClick={() => rotate(row.id)}
                      disabled={rotating === row.id}
                      className="px-3 py-1.5 rounded bg-amber-500 text-black text-xs font-medium hover:opacity-90 disabled:opacity-50"
                    >
                      {rotating === row.id ? "rotating..." : "confirm rotate"}
                    </button>
                    <button
                      onClick={() => {
                        setRotateId(null);
                        setRotateError(null);
                      }}
                      className="px-3 py-1.5 rounded border border-[var(--color-line)] text-xs"
                    >
                      cancel
                    </button>
                    {rotateError && (
                      <span className="text-[11px] text-red-500">{rotateError}</span>
                    )}
                  </div>
                ) : ipId === row.id ? (
                  <div className="flex flex-col gap-2 w-full sm:w-[28rem]">
                    <label
                      htmlFor={`ip-cidrs-${row.id}`}
                      className="text-[11px] text-[var(--color-muted)] font-mono uppercase tracking-wider"
                    >
                      allowed cidrs (one per line, ipv4 or ipv6)
                    </label>
                    <textarea
                      id={`ip-cidrs-${row.id}`}
                      value={ipDraft}
                      onChange={(e) => setIpDraft(e.target.value)}
                      rows={3}
                      spellCheck={false}
                      placeholder={"203.0.113.0/24\n2001:db8::/32"}
                      className="w-full px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-xs font-mono"
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        onClick={() => saveIpAllowlist(row.id)}
                        disabled={ipSaving === row.id}
                        className="px-3 py-1.5 rounded bg-[var(--color-accent)] text-[var(--color-bg)] text-xs font-medium hover:opacity-90 disabled:opacity-50"
                      >
                        {ipSaving === row.id ? "saving..." : "save"}
                      </button>
                      <button
                        onClick={() => setIpDraft("")}
                        className="px-3 py-1.5 rounded border border-[var(--color-line)] text-xs"
                        title="Clear the editor (empty list removes the IP restriction when saved)"
                      >
                        clear
                      </button>
                      <button
                        onClick={() => {
                          setIpId(null);
                          setIpDraft("");
                          setIpError(null);
                        }}
                        className="px-3 py-1.5 rounded border border-[var(--color-line)] text-xs"
                      >
                        cancel
                      </button>
                      {ipError && (
                        <span className="text-[11px] text-red-500 break-all">{ipError}</span>
                      )}
                    </div>
                    <p className="text-[11px] text-[var(--color-muted)]">
                      Empty list removes the restriction. Step-up MFA is required when the workspace enforces it.
                    </p>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        setRotateId(row.id);
                        setRotateGrace("30");
                        setRotateError(null);
                      }}
                      disabled={row.expired}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded border border-[var(--color-line)] text-xs hover:bg-[var(--color-bg)] disabled:opacity-50"
                      aria-label={`Rotate ${row.name}`}
                      title="Mint a new secret for this token. The old secret keeps working briefly so deployed clients can swap without downtime."
                    >
                      <ShieldCheck size={12} weight="bold" /> rotate
                    </button>
                    <button
                      onClick={() => {
                        setIpId(row.id);
                        setIpDraft((row.ip_cidrs ?? []).join("\n"));
                        setIpError(null);
                      }}
                      disabled={row.expired}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded border border-[var(--color-line)] text-xs hover:bg-[var(--color-bg)] disabled:opacity-50"
                      aria-label={`Edit IP allowlist for ${row.name}`}
                      title="Restrict this token to specific source IP ranges (CIDR). Empty list means usable from any IP."
                    >
                      <GlobeHemisphereWest size={12} weight="duotone" /> ip allowlist
                    </button>
                    <button
                      onClick={() => setConfirmId(row.id)}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded border border-[var(--color-line)] text-xs hover:bg-[var(--color-bg)]"
                      aria-label={`Revoke ${row.name}`}
                    >
                      <Trash size={12} weight="bold" /> revoke
                    </button>
                  </div>
                )}
              </div>
            ))}
      </section>
    </main>
  );
}
