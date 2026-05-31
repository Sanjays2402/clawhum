"use client";

/**
 * Step-up MFA management (TOTP).
 *
 * Enrollment is per-actor (per API key). Once an actor verifies a
 * TOTP authenticator, destructive admin endpoints (revoke key, delete
 * webhook, edit IP allowlist, edit destination allowlist, delete /me
 * data) require an ``X-MFA-Code`` header alongside the API key on
 * every call.
 *
 * The recovery codes are shown exactly once at verification time.
 * They are also the only way to disable MFA if the authenticator is
 * lost, so the UI nudges the user to print or copy them before
 * closing the success panel.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ShieldWarning,
  ArrowLeft,
  Copy,
  Check,
  Warning,
  Lock,
  LockOpen,
  Key,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface StatusResp {
  enrolled: boolean;
  verified: boolean;
  recovery_remaining: number;
  created_at?: number | null;
  verified_at?: number | null;
}

interface EnrollResp {
  secret: string;
  otpauth: string;
  digits: number;
  period: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; status: StatusResp }
  | { kind: "error"; status: number; message: string };

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

export default function SecurityPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [enrolling, setEnrolling] = useState(false);
  const [enroll, setEnroll] = useState<EnrollResp | null>(null);
  const [verifyCode, setVerifyCode] = useState("");
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [copiedSecret, setCopiedSecret] = useState(false);
  const [copiedRecovery, setCopiedRecovery] = useState(false);
  const [disableCode, setDisableCode] = useState("");
  const [disableBusy, setDisableBusy] = useState(false);
  const [disableError, setDisableError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/mfa/status", { headers: authHeaders(), cache: "no-store" });
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as StatusResp;
      setState({ kind: "ready", status: data });
    } catch (err) {
      setState({ kind: "error", status: 0, message: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, storedKey]);

  async function onEnroll() {
    setEnrolling(true);
    setVerifyError(null);
    try {
      const r = await fetch("/api/mfa/enroll", {
        method: "POST",
        headers: authHeaders(),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setVerifyError(body.detail || `Request failed (${r.status})`);
        return;
      }
      const data = (await r.json()) as EnrollResp;
      setEnroll(data);
      setVerifyCode("");
    } finally {
      setEnrolling(false);
    }
  }

  async function onVerify(e: React.FormEvent) {
    e.preventDefault();
    if (!verifyCode.trim()) return;
    setVerifyBusy(true);
    setVerifyError(null);
    try {
      const r = await fetch("/api/mfa/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ code: verifyCode.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setVerifyError(body.detail || `Invalid code (${r.status})`);
        return;
      }
      const data = (await r.json()) as { ok: boolean; recovery_codes: string[] };
      setRecovery(data.recovery_codes || []);
      setEnroll(null);
      setVerifyCode("");
      await refresh();
    } finally {
      setVerifyBusy(false);
    }
  }

  async function onDisable(e: React.FormEvent) {
    e.preventDefault();
    if (!disableCode.trim()) return;
    setDisableBusy(true);
    setDisableError(null);
    try {
      const r = await fetch("/api/mfa", {
        method: "DELETE",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ code: disableCode.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setDisableError(body.detail || `Disable failed (${r.status})`);
        return;
      }
      setDisableCode("");
      setRecovery(null);
      await refresh();
    } finally {
      setDisableBusy(false);
    }
  }

  async function copy(text: string, which: "secret" | "recovery") {
    try {
      await navigator.clipboard.writeText(text);
      if (which === "secret") {
        setCopiedSecret(true);
        setTimeout(() => setCopiedSecret(false), 1500);
      } else {
        setCopiedRecovery(true);
        setTimeout(() => setCopiedRecovery(false), 1500);
      }
    } catch {
      /* clipboard unavailable, fall back to user selecting the text */
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:py-14">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        <ArrowLeft size={14} weight="duotone" />
        Settings
      </Link>

      <header className="mt-4 flex items-start gap-3">
        <ShieldCheck size={28} weight="duotone" className="mt-1 text-emerald-500" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Step-up MFA</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Add a TOTP authenticator. Once verified, destructive admin endpoints
            require a fresh code sent as <code className="font-mono text-[12px]">X-MFA-Code</code>.
          </p>
        </div>
      </header>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-3" aria-busy="true">
          <div className="h-24 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
          <div className="h-12 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
          <Warning size={18} weight="duotone" className="mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">Failed to load MFA status</div>
            <div className="font-mono text-xs opacity-80">
              {state.status} {state.message}
            </div>
            <button
              type="button"
              onClick={refresh}
              className="mt-2 inline-flex items-center rounded border border-red-300 px-2 py-1 text-xs hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900/30"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {state.kind === "ready" && (
        <section className="mt-8 space-y-6">
          {/* Status card */}
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900/50">
            {state.status.verified ? (
              <>
                <Lock size={18} weight="duotone" className="text-emerald-500" />
                <span className="font-medium">MFA active for this API key</span>
                <span className="ml-auto font-mono text-xs text-zinc-500 dark:text-zinc-400">
                  {state.status.recovery_remaining} recovery codes left
                </span>
              </>
            ) : state.status.enrolled ? (
              <>
                <ShieldWarning size={18} weight="duotone" className="text-amber-500" />
                <span className="font-medium">Enrollment pending verification</span>
              </>
            ) : (
              <>
                <LockOpen size={18} weight="duotone" className="text-zinc-400" />
                <span className="font-medium">MFA is not enabled for this API key</span>
              </>
            )}
          </div>

          {state.status.verified_at && (
            <div className="text-xs text-zinc-500 dark:text-zinc-400">
              Verified {formatTs(state.status.verified_at)}
            </div>
          )}

          {/* Recovery codes (shown once after verify) */}
          {recovery && (
            <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
              <div className="flex items-center gap-2">
                <Key size={18} weight="duotone" className="text-emerald-600" />
                <h2 className="text-sm font-medium text-emerald-900 dark:text-emerald-200">
                  Save these recovery codes
                </h2>
                <button
                  type="button"
                  onClick={() => copy(recovery.join("\n"), "recovery")}
                  className="ml-auto inline-flex items-center gap-1 rounded border border-emerald-300 bg-white px-2 py-1 text-xs hover:bg-emerald-100 dark:border-emerald-800 dark:bg-zinc-950 dark:hover:bg-emerald-900/30"
                >
                  {copiedRecovery ? <Check size={12} /> : <Copy size={12} />}
                  {copiedRecovery ? "copied" : "copy all"}
                </button>
              </div>
              <p className="mt-1 text-xs text-emerald-800 dark:text-emerald-300">
                Each code works once. They are the only way back in if the
                authenticator is lost. This list will not be shown again.
              </p>
              <ul className="mt-3 grid grid-cols-2 gap-1 font-mono text-[12px] text-emerald-900 dark:text-emerald-100 sm:grid-cols-2">
                {recovery.map((c) => (
                  <li key={c} className="rounded bg-white/70 px-2 py-1 dark:bg-zinc-950/60">
                    {c}
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={() => setRecovery(null)}
                className="mt-3 text-xs text-emerald-800 underline hover:no-underline dark:text-emerald-300"
              >
                I have stored them
              </button>
            </div>
          )}

          {/* Enroll flow */}
          {!state.status.verified && !enroll && (
            <div className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <h2 className="text-sm font-medium">Enroll an authenticator</h2>
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                Generate a fresh TOTP secret. Scan it into 1Password, Authy,
                Google Authenticator, or any RFC 6238 client.
              </p>
              <button
                type="button"
                onClick={onEnroll}
                disabled={enrolling || !storedKey}
                className="mt-3 inline-flex items-center gap-2 rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:hover:bg-zinc-900"
              >
                <ShieldCheck size={14} weight="duotone" />
                {enrolling ? "Generating..." : state.status.enrolled ? "Restart enrollment" : "Start enrollment"}
              </button>
              {!storedKey && (
                <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
                  Save an API key on the <Link href="/settings" className="underline">settings page</Link> first.
                </p>
              )}
              {verifyError && (
                <p className="mt-2 text-xs text-red-700 dark:text-red-400">{verifyError}</p>
              )}
            </div>
          )}

          {/* Verify pending enrollment */}
          {enroll && (
            <form
              onSubmit={onVerify}
              className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/30"
            >
              <h2 className="text-sm font-medium text-amber-900 dark:text-amber-100">
                Pair your authenticator
              </h2>
              <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
                Add this secret to your TOTP app, then type the first 6-digit code.
              </p>

              <div className="mt-3 rounded border border-amber-200 bg-white p-3 dark:border-amber-900 dark:bg-zinc-950">
                <div className="text-[10px] uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
                  Shared secret
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <code className="font-mono text-sm break-all">{enroll.secret}</code>
                  <button
                    type="button"
                    onClick={() => copy(enroll.secret, "secret")}
                    className="ml-auto inline-flex items-center gap-1 rounded border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
                  >
                    {copiedSecret ? <Check size={12} /> : <Copy size={12} />}
                    {copiedSecret ? "copied" : "copy"}
                  </button>
                </div>
                <div className="mt-2 text-[10px] uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
                  otpauth URI
                </div>
                <code className="mt-1 block break-all font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
                  {enroll.otpauth}
                </code>
              </div>

              <label htmlFor="verify-code" className="mt-4 block text-xs font-medium text-amber-900 dark:text-amber-100">
                Verification code
              </label>
              <input
                id="verify-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={8}
                value={verifyCode}
                onChange={(e) => setVerifyCode(e.target.value)}
                placeholder="123456"
                className="mt-1 w-40 rounded border border-amber-300 bg-white px-3 py-2 font-mono text-base tracking-widest focus:border-amber-500 focus:outline-none dark:border-amber-800 dark:bg-zinc-950"
              />

              {verifyError && (
                <p className="mt-2 text-xs text-red-700 dark:text-red-400">{verifyError}</p>
              )}

              <div className="mt-3 flex items-center gap-2">
                <button
                  type="submit"
                  disabled={verifyBusy || !verifyCode.trim()}
                  className="inline-flex items-center gap-2 rounded bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  {verifyBusy ? "Verifying..." : "Verify and activate"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setEnroll(null);
                    setVerifyCode("");
                    setVerifyError(null);
                  }}
                  className="text-xs text-amber-800 underline hover:no-underline dark:text-amber-300"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

          {/* Disable flow */}
          {state.status.verified && (
            <form
              onSubmit={onDisable}
              className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
            >
              <h2 className="text-sm font-medium">Disable MFA</h2>
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                Requires a current TOTP code or one recovery code so a leaked
                API key cannot turn the second factor off on its own.
              </p>
              <label htmlFor="disable-code" className="mt-3 block text-xs font-medium">
                Current code or recovery code
              </label>
              <input
                id="disable-code"
                value={disableCode}
                onChange={(e) => setDisableCode(e.target.value)}
                placeholder="123456 or ABCDE-FGHIJ"
                className="mt-1 w-64 rounded border border-zinc-300 bg-white px-3 py-2 font-mono text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950"
              />
              {disableError && (
                <p className="mt-2 text-xs text-red-700 dark:text-red-400">{disableError}</p>
              )}
              <button
                type="submit"
                disabled={disableBusy || !disableCode.trim()}
                className="mt-3 inline-flex items-center gap-2 rounded border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
              >
                {disableBusy ? "Disabling..." : "Disable MFA"}
              </button>
            </form>
          )}

          {/* Sudo mode link */}
          <div className="rounded-lg border border-zinc-200 p-4 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
            <div className="mb-1 text-[10px] uppercase tracking-widest text-zinc-500">
              Step-up session
            </div>
            <p>
              Verify once and skip retyping codes for a short window.{" "}
              <Link href="/settings/security/sudo" className="underline hover:text-zinc-900 dark:hover:text-zinc-100">
                Open sudo mode
              </Link>
            </p>
          </div>

          {/* What this gates */}
          <div className="rounded-lg border border-zinc-200 p-4 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
            <div className="mb-1 text-[10px] uppercase tracking-widest text-zinc-500">
              Endpoints that require X-MFA-Code once enrolled
            </div>
            <ul className="space-y-0.5 font-mono">
              <li>DELETE /keys/&#123;id&#125;</li>
              <li>DELETE /webhooks/&#123;id&#125;</li>
              <li>PUT /webhooks/destination-allowlist</li>
              <li>POST and DELETE /ip-allowlist</li>
              <li>DELETE /me</li>
            </ul>
          </div>
        </section>
      )}
    </main>
  );
}
