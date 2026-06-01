"use client";

/**
 * Per-workspace export signing key administration.
 *
 * Workspace data exports (the GDPR/SOC2 download at
 * /v1/privacy/workspace-export) are signed with a per-workspace
 * HMAC-SHA256 key. Compliance reviewers can verify months later that
 * an archived bundle was produced by clawhum for this workspace and
 * was not tampered with, via /v1/privacy/workspace-export/verify.
 *
 * Mint creates the initial key. Rotate replaces it and keeps the
 * previous key verifying for a 14 day grace window. Reveal redisplays
 * the active secret in case the post-mint copy was lost. All mutating
 * actions require admin role plus a fresh MFA code and are audited.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Warning,
  CheckCircle,
  Key,
  ArrowsClockwise,
  Eye,
  ShieldCheck,
  Copy,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface KeyView {
  tenant_id?: string;
  exists: boolean;
  algorithm: string;
  verify_endpoint: string;
  key_id?: string;
  created_at?: number;
  created_by?: string;
  rotated_at?: number;
  prior_key_id?: string;
  prior_grace_expires_at?: number;
  prior_in_grace?: boolean;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; key: KeyView }
  | { kind: "error"; status: number; message: string };

function authHeaders(mfa?: string): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  if (mfa) h["X-MFA-Code"] = mfa;
  return h;
}

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

export default function ExportSigningPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [mfa, setMfa] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/export-signing", {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const key = (await r.json()) as KeyView;
      setState({ kind: "ready", key });
    } catch (e: any) {
      setState({ kind: "error", status: 0, message: e?.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function callAction(action: "mint" | "rotate" | "reveal", successMessage: string) {
    setActionError(null);
    setFlash(null);
    setRevealedSecret(null);
    setCopied(false);
    if (!mfa.trim()) {
      setActionError("MFA code required");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(`/api/export-signing/${action}`, {
        method: "POST",
        headers: authHeaders(mfa),
      });
      if (!r.ok) {
        const txt = await r.text();
        setActionError(`${r.status}: ${txt || r.statusText}`);
        return;
      }
      const data = await r.json();
      if (action === "mint" && data.minted === false) {
        setActionError(data.reason || "key already exists; use rotate");
      } else if (data.exists === false) {
        setActionError(data.reason || "no signing key for tenant; mint first");
      } else {
        if (typeof data.secret === "string") {
          setRevealedSecret(data.secret);
        }
        setFlash(successMessage);
        setMfa("");
      }
      await refresh();
    } catch (e: any) {
      setActionError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function copySecret() {
    if (!revealedSecret) return;
    try {
      await navigator.clipboard.writeText(revealedSecret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard may be unavailable
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <div className="mb-6 flex items-center gap-2 text-sm text-zinc-500">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          <ArrowLeft weight="duotone" className="h-4 w-4" />
          Settings
        </Link>
      </div>

      <div className="mb-8 flex items-start gap-3">
        <ShieldCheck weight="duotone" className="mt-1 h-7 w-7 text-emerald-500" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Export signing key
          </h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            Every workspace data export download is signed with a
            per-workspace HMAC-SHA256 key. Compliance reviewers can
            verify months later that an archived bundle was produced
            by clawhum for this workspace and was not modified. Rotate
            any time; the previous key keeps verifying for 14 days so
            bundles already in flight still pass. Admin role plus MFA
            required to mint, rotate, or reveal.
          </p>
        </div>
      </div>

      {state.kind === "loading" && (
        <div className="space-y-2" aria-busy="true">
          <div className="h-12 animate-pulse rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/40" />
          <div className="h-32 animate-pulse rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/40" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <Warning weight="duotone" className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <div className="font-medium">
              {state.status === 401
                ? "Invalid API key"
                : state.status === 403
                  ? "Reader role required"
                  : "Could not load signing key"}
            </div>
            <div className="mt-1 break-words">{state.message}</div>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <div className="space-y-6">
          {/* Current key card */}
          <section className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="mb-3 flex items-center gap-2">
              <Key weight="duotone" className="h-5 w-5 text-zinc-500" />
              <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                Active signing key
              </h2>
            </div>
            {state.key.exists ? (
              <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">key id</dt>
                  <dd className="mt-0.5 font-mono text-zinc-900 dark:text-zinc-100">
                    {state.key.key_id}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">algorithm</dt>
                  <dd className="mt-0.5 font-mono text-zinc-900 dark:text-zinc-100">
                    {state.key.algorithm}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">created</dt>
                  <dd className="mt-0.5 text-zinc-900 dark:text-zinc-100">
                    {formatTs(state.key.created_at)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-zinc-500">created by</dt>
                  <dd className="mt-0.5 text-zinc-900 dark:text-zinc-100">
                    {state.key.created_by || "unknown"}
                  </dd>
                </div>
                {state.key.rotated_at ? (
                  <>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-zinc-500">
                        last rotated
                      </dt>
                      <dd className="mt-0.5 text-zinc-900 dark:text-zinc-100">
                        {formatTs(state.key.rotated_at)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-zinc-500">
                        previous key
                      </dt>
                      <dd className="mt-0.5 font-mono text-zinc-900 dark:text-zinc-100">
                        {state.key.prior_key_id}
                        {state.key.prior_in_grace ? (
                          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                            in grace until {formatTs(state.key.prior_grace_expires_at)}
                          </span>
                        ) : (
                          <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                            expired
                          </span>
                        )}
                      </dd>
                    </div>
                  </>
                ) : null}
              </dl>
            ) : (
              <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-6 text-center text-sm text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-400">
                No signing key yet. Mint one to start signing exports.
                Bundles produced before a key exists are not signed but
                are still downloadable.
              </div>
            )}
            <div className="mt-4 text-xs text-zinc-500">
              Verify endpoint: <code className="font-mono">{state.key.verify_endpoint}</code>
            </div>
          </section>

          {/* Revealed secret card */}
          {revealedSecret && (
            <section className="rounded-xl border border-emerald-300 bg-emerald-50 p-5 dark:border-emerald-900/50 dark:bg-emerald-950/30">
              <div className="mb-2 flex items-center gap-2">
                <CheckCircle weight="duotone" className="h-5 w-5 text-emerald-600" />
                <h2 className="text-sm font-medium text-emerald-900 dark:text-emerald-200">
                  Signing secret (shown once unless you reveal again)
                </h2>
              </div>
              <div className="flex items-stretch gap-2">
                <code className="block flex-1 break-all rounded-md border border-emerald-300 bg-white px-3 py-2 font-mono text-xs text-zinc-900 dark:border-emerald-900/50 dark:bg-zinc-950 dark:text-zinc-100">
                  {revealedSecret}
                </code>
                <button
                  type="button"
                  onClick={copySecret}
                  className="inline-flex items-center gap-1 rounded-md border border-emerald-400 bg-white px-3 text-xs font-medium text-emerald-900 hover:bg-emerald-50 dark:border-emerald-700 dark:bg-zinc-950 dark:text-emerald-200 dark:hover:bg-emerald-950/50"
                >
                  <Copy weight="duotone" className="h-4 w-4" />
                  {copied ? "copied" : "copy"}
                </button>
              </div>
              <p className="mt-2 text-xs text-emerald-800 dark:text-emerald-300">
                Store this in your secrets manager. clawhum keeps the
                secret server side for verification, but only redisplays
                it through the reveal action above.
              </p>
            </section>
          )}

          {/* Action panel */}
          <section className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="mb-3 text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Actions
            </h2>
            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              MFA code
            </label>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={mfa}
              onChange={(e) => setMfa(e.target.value)}
              placeholder="123456"
              className="mt-1 w-40 rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            />

            {flash && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-200">
                <CheckCircle weight="duotone" className="mt-0.5 h-4 w-4 shrink-0" />
                <div>{flash}</div>
              </div>
            )}
            {actionError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
                <Warning weight="duotone" className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="break-words">{actionError}</div>
              </div>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              {!state.key.exists && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => callAction("mint", "Signing key minted. Future exports will include x-clawhum-export-signature headers.")}
                  className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                >
                  <Key weight="duotone" className="h-4 w-4" />
                  Mint key
                </button>
              )}
              {state.key.exists && (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => callAction("rotate", "Key rotated. Previous key verifies for 14 more days.")}
                    className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                  >
                    <ArrowsClockwise weight="duotone" className="h-4 w-4" />
                    Rotate key
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => callAction("reveal", "Active signing secret redisplayed.")}
                    className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:bg-zinc-900"
                  >
                    <Eye weight="duotone" className="h-4 w-4" />
                    Reveal secret
                  </button>
                </>
              )}
            </div>
          </section>

          {/* Verify how-to */}
          <section className="rounded-xl border border-zinc-200 bg-zinc-50 p-5 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-300">
            <h2 className="mb-2 font-medium text-zinc-900 dark:text-zinc-100">
              Verifying an archived bundle
            </h2>
            <p className="mb-2">
              Download a workspace export from{" "}
              <code className="font-mono text-xs">/v1/privacy/workspace-export</code>.
              The signature appears in the response headers
              {" "}
              <code className="font-mono text-xs">x-clawhum-export-signature</code>,{" "}
              <code className="font-mono text-xs">x-clawhum-export-key-id</code>, and{" "}
              <code className="font-mono text-xs">x-clawhum-export-signature-alg</code>,
              and inside <code className="font-mono text-xs">manifest.json</code>.
              To verify later, POST the manifest dict to{" "}
              <code className="font-mono text-xs">{state.key.verify_endpoint}</code>.
            </p>
            <pre className="overflow-x-auto rounded-md border border-zinc-200 bg-white p-3 font-mono text-xs text-zinc-900 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100">{`curl -X POST http://127.0.0.1:7451${state.key.verify_endpoint} \\
  -H "X-API-Key: $API_KEY" \\
  -H "Content-Type: application/json" \\
  --data @manifest.json`}</pre>
          </section>
        </div>
      )}
    </main>
  );
}
