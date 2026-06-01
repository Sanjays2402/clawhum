"use client";

import { useEffect, useState } from "react";
import {
  Key,
  ShieldCheck,
  Gauge,
  Copy,
  Check,
  Eye,
  EyeSlash,
  Trash,
  Warning,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, maskKey, setApiKey, useApiKey } from "@/lib/apiKey";
import { resetOnboarding } from "@/components/OnboardingTour";
import PrivacySection from "@/components/PrivacySection";

interface MeResponse {
  tenant_id: string;
  key_name: string;
  roles: string[];
  rate_limit_per_minute: number;
  auth_mode: "open" | "key";
  masked_key: string;
}

type IdentityState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; me: MeResponse }
  | { kind: "error"; status: number; message: string };

function curlExample(origin: string, key: string): string {
  const k = key || "YOUR_API_KEY";
  return `curl -X POST ${origin}/api/match \\\n  -H "X-API-Key: ${k}" \\\n  -F "audio=@hum.wav"`;
}

export default function SettingsPage() {
  const [stored, save] = useApiKey();
  const [draft, setDraft] = useState("");
  const [reveal, setReveal] = useState(false);
  const [copied, setCopied] = useState<"key" | "curl" | null>(null);
  const [identity, setIdentity] = useState<IdentityState>({ kind: "idle" });
  const [origin, setOrigin] = useState("http://127.0.0.1:7452");

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    setDraft(stored);
  }, [stored]);

  async function probe() {
    setIdentity({ kind: "loading" });
    try {
      const headers: Record<string, string> = {};
      const k = getApiKey();
      if (k) headers["X-API-Key"] = k;
      const r = await fetch("/api/me", { headers, cache: "no-store" });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        setIdentity({
          kind: "error",
          status: r.status,
          message: text || r.statusText,
        });
        return;
      }
      const me = (await r.json()) as MeResponse;
      setIdentity({ kind: "ok", me });
    } catch (e: any) {
      setIdentity({
        kind: "error",
        status: 0,
        message: e?.message || String(e),
      });
    }
  }

  // Re-probe whenever the stored key changes.
  useEffect(() => {
    probe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stored]);

  async function copy(label: "key" | "curl", text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      /* clipboard may be blocked; silent */
    }
  }

  const dirty = draft !== stored;
  const rpm = identity.kind === "ok" ? identity.me.rate_limit_per_minute : 0;
  // Local quota meter: how many same-origin calls we've fired since the
  // page loaded. Real per-tenant counters live server side under
  // /metrics; this gives the user immediate feedback against rpm.
  const [windowCount, setWindowCount] = useState(0);
  useEffect(() => {
    const w = window as any;
    if (w.__clawhumApiCount != null) {
      setWindowCount(w.__clawhumApiCount);
      return;
    }
    let count = 0;
    const origFetch = window.fetch.bind(window);
    window.fetch = async (input: any, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input?.url || "";
      if (url.startsWith("/api/") || url.startsWith(window.location.origin + "/api/")) {
        count += 1;
        w.__clawhumApiCount = count;
        setWindowCount(count);
      }
      return origFetch(input, init);
    };
  }, []);

  const usagePct = rpm > 0 ? Math.min(100, Math.round((windowCount / rpm) * 100)) : 0;

  return (
    <div className="px-4 py-4 space-y-4 max-w-[1100px]">
      <div className="flex items-center justify-between">
        <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
          settings / api key + usage
        </h1>
        <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          stored locally / never synced
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* API key */}
        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Key size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            <span className="label-xs">api key</span>
            {stored && (
              <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                saved / {maskKey(stored)}
              </span>
            )}
          </div>

          <label className="block space-y-1">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
              x-api-key header value
            </span>
            <div className="flex gap-1">
              <input
                aria-label="API key"
                type={reveal ? "text" : "password"}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="sk_live_..."
                className="flex-1 bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setReveal((r) => !r)}
                aria-label={reveal ? "Hide key" : "Show key"}
                className="border border-[var(--color-line)] px-2 hover:bg-[var(--color-panel)]"
              >
                {reveal ? <EyeSlash size={14} weight="duotone" /> : <Eye size={14} weight="duotone" />}
              </button>
            </div>
          </label>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => save(draft.trim())}
              disabled={!dirty}
              className="border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest hover:bg-[rgba(0,255,140,0.06)] disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {stored ? "update" : "save"}
            </button>
            <button
              type="button"
              onClick={() => copy("key", stored)}
              disabled={!stored}
              className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] disabled:opacity-30 inline-flex items-center gap-1.5"
            >
              {copied === "key" ? <Check size={12} weight="duotone" /> : <Copy size={12} weight="duotone" />}
              {copied === "key" ? "copied" : "copy"}
            </button>
            <button
              type="button"
              onClick={() => { save(""); setDraft(""); }}
              disabled={!stored}
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-amber)] hover:bg-[rgba(245,158,11,0.06)] disabled:opacity-30 inline-flex items-center gap-1.5"
            >
              <Trash size={12} weight="duotone" />
              clear
            </button>
          </div>

          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            keys live in localStorage on this device. every /api/* call from this browser sends it as X-API-Key. clear before signing out of a shared machine.
          </p>
        </section>

        {/* Identity */}
        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex items-center gap-2">
            <ShieldCheck size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            <span className="label-xs">identity</span>
            <button
              type="button"
              onClick={probe}
              className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
            >
              refresh
            </button>
          </div>

          {identity.kind === "loading" && (
            <div className="space-y-2" aria-busy="true">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-3 bg-[var(--color-panel)] animate-pulse rounded-[2px]" />
              ))}
            </div>
          )}

          {identity.kind === "error" && (
            <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] p-2 font-mono text-[11px] text-[var(--color-amber)] flex items-start gap-2">
              <Warning size={14} weight="duotone" className="mt-0.5" />
              <div>
                <div className="uppercase tracking-widest">
                  {identity.status === 401 ? "invalid key" : identity.status === 0 ? "api unreachable" : `error / ${identity.status}`}
                </div>
                <div className="text-[10px] text-[var(--color-dim)] mt-0.5 break-words">{identity.message}</div>
              </div>
            </div>
          )}

          {identity.kind === "ok" && (
            <div className="font-mono text-[11px] grid grid-cols-2 gap-2">
              <span className="text-[var(--color-muted)]">tenant</span>
              <span className="text-[var(--color-text)]">{identity.me.tenant_id}</span>
              <span className="text-[var(--color-muted)]">key name</span>
              <span className="text-[var(--color-text)]">{identity.me.key_name}</span>
              <span className="text-[var(--color-muted)]">roles</span>
              <span className="text-[var(--color-text)]">{identity.me.roles.join(", ") || "none"}</span>
              <span className="text-[var(--color-muted)]">auth mode</span>
              <span className="text-[var(--color-text)]">
                {identity.me.auth_mode === "open" ? (
                  <span className="text-[var(--color-amber)]">open / dev</span>
                ) : (
                  <span className="text-[var(--color-phosphor)]">key</span>
                )}
              </span>
              <span className="text-[var(--color-muted)]">rate limit</span>
              <span className="text-[var(--color-text)] tabular-nums">{identity.me.rate_limit_per_minute} / min</span>
            </div>
          )}
        </section>

        {/* Usage meter */}
        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Gauge size={14} weight="duotone" className="text-[var(--color-phosphor)]" />
            <span className="label-xs">usage / this session</span>
            <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)] tabular-nums">
              {windowCount} / {rpm || "—"}
            </span>
          </div>
          <div className="h-2 bg-[var(--color-bg)] border border-[var(--color-line)] overflow-hidden">
            <div
              className={`h-full transition-[width] duration-300 ${usagePct >= 90 ? "bg-[var(--color-amber)]" : "bg-[var(--color-phosphor)]"}`}
              style={{ width: `${usagePct}%` }}
              role="progressbar"
              aria-valuenow={windowCount}
              aria-valuemin={0}
              aria-valuemax={rpm || 100}
            />
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            counts every /api/* call since page load. server-enforced rate limit comes from your key configuration. exceeding it returns 429.
          </p>
        </section>

        {/* Curl */}
        <section className="panel rounded-[2px] p-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="label-xs">try the api</span>
            <button
              type="button"
              onClick={() => copy("curl", curlExample(origin, stored))}
              className="ml-auto border border-[var(--color-line)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)] inline-flex items-center gap-1.5"
            >
              {copied === "curl" ? <Check size={11} weight="duotone" /> : <Copy size={11} weight="duotone" />}
              {copied === "curl" ? "copied" : "copy"}
            </button>
          </div>
          <pre className="bg-[var(--color-bg)] border border-[var(--color-line)] p-2 font-mono text-[11px] text-[var(--color-text)] whitespace-pre-wrap break-all">
{curlExample(origin, stored)}
          </pre>
          {!stored && (
            <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
              save an api key above and the snippet will fill in automatically.
            </p>
          )}
        </section>

        <PrivacySection />

        {/* Workspace IP allowlist */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">sandbox / dry run</span>
            <a
              href="/settings/sandbox"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              open
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            preview any destructive call before it runs. every DELETE endpoint accepts ?dry_run=true and returns what would be removed without touching storage.
          </p>
        </section>

        {/* Two-factor authentication */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">two-factor authentication</span>
            <a
              href="/settings/security"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            enroll a TOTP authenticator. once verified, destructive admin endpoints require a fresh X-MFA-Code header alongside the api key.
          </p>
        </section>

        {/* Two-factor authentication */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">two-factor authentication</span>
            <a
              href="/settings/security"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            enroll a TOTP authenticator. once verified, destructive admin endpoints require a fresh X-MFA-Code header alongside the api key.
          </p>
        </section>

        {/* Workspace members */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">workspace members</span>
            <a
              href="/settings/members"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            invite teammates by email, assign roles (admin, writer, reader), and revoke seats when people leave. invite tokens are shown once; destructive changes require admin plus MFA.
          </p>
        </section>

        {/* Workspace quota plan */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">workspace quota</span>
            <a
              href="/settings/quotas"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cap aggregate requests-per-minute and daily quota across every api key in this workspace. pick a plan or set custom ceilings. enforced at the edge with standard X-RateLimit-* headers. admin role plus MFA required to change.
          </p>
        </section>

        {/* Workspace seat license */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">seat license</span>
            <a
              href="/settings/seat-limit"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cap the number of active plus pending members so invites stop at the contracted seat count instead of silently overflowing. the api returns http 402 with a structured upgrade hint when full. admin role plus MFA required to change.
          </p>
        </section>

        {/* Workspace data residency */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">workspace closure</span>
            <a
              href="/settings/workspace-closure"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            schedule a wind-down for this workspace. during the grace window every mutating request returns http 423 so customer data is preserved read only for export. after the deadline non-export reads return http 410. cancel any time before the deadline. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace data residency */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">legal hold</span>
            <a
              href="/settings/legal-holds"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            freeze destructive operations on this workspace for litigation or regulatory preservation. retention purges, gdpr erasures, and history deletes return http 423 while a hold is active. reads and exports keep working. admin role plus MFA required to place or release.
          </p>
        </section>

        {/* Workspace data retention */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">data retention</span>
            <a
              href="/settings/retention"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cap how long history, feedback, audit, and webhook delivery rows live in this workspace. reads filter expired rows immediately; enforce hard deletes them. preview with dry run first. admin role plus MFA required to change or sweep.
          </p>
        </section>

        {/* Workspace data residency */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">data residency</span>
            <a
              href="/settings/residency"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            pin this workspace to us, eu, or apac. mutating requests against an out-of-region node return http 451. reads stay open. admin role plus MFA required to change.
          </p>
        </section>

        {/* Workspace IP allowlist */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">workspace ip allowlist</span>
            <a
              href="/settings/ip-allowlist"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            restrict workspace api access to a list of trusted cidr ranges. admin role required.
          </p>
        </section>

        {/* Workspace trusted reverse proxies */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">trusted proxies</span>
            <a
              href="/settings/trusted-proxies"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cidrs the api trusts to set x-forwarded-for. without an entry covering your ingress, the workspace ip allowlist sees the socket peer and ignores any spoofed header. admin role required.
          </p>
        </section>

        {/* Workspace invite domain allowlist */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">invite domains</span>
            <a
              href="/settings/invite-domains"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            pin which email domains can hold a seat. applies to manual invites, sso auto join, and scim provisioning. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace PAT scope policy */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">scope policy</span>
            <a
              href="/settings/scope-policy"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            pin the maximum scope set this workspace may mint on a personal access token. blocks even admins from minting write:keys or admin scope when not permitted. admin role plus MFA required.
          </p>
        </section>

        {/* Allowed authentication methods */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">auth methods</span>
            <a
              href="/settings/auth-methods"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            choose which credential classes this workspace accepts: deploy time env keys, personal access tokens, scim bearer tokens. disabled methods are rejected at the auth layer and pat mint is blocked when pats are off. admin role plus MFA required.
          </p>
        </section>

        {/* Export signing key */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">export signing</span>
            <a
              href="/settings/export-signing"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            sign every workspace data export with a per-workspace HMAC-SHA256 key so compliance reviewers can prove months later that an archived bundle was produced by clawhum for this workspace and was not modified. rotate any time; previous key keeps verifying for 14 days. admin role plus MFA required to mint or rotate.
          </p>
        </section>

        {/* Workspace PAT concurrency cap */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">pat concurrency</span>
            <a
              href="/settings/pat-concurrency"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cap the number of live personal access tokens this workspace may hold at once. mints that would breach the cap fail with a structured 429. bounds blast radius and stops credential sprawl. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace PAT secret prefix */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">pat secret prefix</span>
            <a
              href="/settings/pat-secret-prefix"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            shape new pat secrets as pat_&lt;workspace_prefix&gt;_&lt;random&gt; so your secret scanner can attribute a leaked token to this workspace without paging every other clawhum customer. ships with a copy-pasteable regex you can hand to github secret scanning or trufflehog. existing tokens keep working; rotate to adopt the new shape. admin role plus MFA required.
          </p>
        </section>

        {/* PAT expiry warning */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">pat expiry warning</span>
            <a
              href="/settings/pat-expiry-warning"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            attach standards-based sunset and deprecation headers to every response authenticated by a pat that is within N days of expiry, so SDKs and CI pipelines can rotate before 03:00 outage night. tunable per workspace; off by default. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace embed origin allowlist */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">embed origins</span>
            <a
              href="/settings/embed-origins"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            lock down which sites may frame your workspace share embeds and call the oembed endpoint. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace security and breach notification contacts */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">security contacts</span>
            <a
              href="/settings/security-contacts"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            the people we will reach during a security incident or personal data breach. one primary contact is paged first. required for GDPR article 33 notifications and SOC2 incident communication. admin role plus MFA required.
          </p>
        </section>

        {/* Data subject access requests */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">data subject requests</span>
            <a
              href="/settings/dsar"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            intake and tracking for GDPR article 15 / 17 / 20 and CCPA section 1798.100 requests. each request gets a statutory due date and overdue items rise to the top of the queue. every state change is tenant scoped and audit logged. admin role plus MFA required.
          </p>
        </section>

        {/* Per-workspace vendor support access grants */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">support access</span>
            <a
              href="/settings/support-access"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            approve named clawhum support staff to touch your workspace for a bounded window with read or write scope. without an active grant, vendor staff get 403 on every request. every action under a grant is recorded in the audit log with the support actor email and grant id, giving you forensic proof for SOC2 CC6.1 and ISO 27001 A.9.2.3. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace SSO */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">single sign on</span>
            <a
              href="/settings/sso"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            wire your workspace to okta, microsoft entra id, google workspace, or any oidc provider. flip on enforce to require sso for everyone in your email domain. admin role plus MFA required.
          </p>
        </section>

        {/* Workspace audit log */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">audit log</span>
            <a
              href="/settings/audit"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              open
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            search and export every mutating request your workspace made. filter by actor, method, path, status, and time window. csv and json downloads for compliance review. admin role required, tenant scoped server side.
          </p>
        </section>

        {/* Audit log forwarding to SIEM */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">audit log forwarding</span>
            <a
              href="/admin/audit-forwarding"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            stream every audit event to your splunk, datadog, sumo, panther, or any HTTPS sink. HMAC-SHA256 signed, retried with backoff, replayable from the delivery log. admin role plus MFA required.
          </p>
        </section>

        {/* Webhook destination policy */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">webhook destinations</span>
            <a
              href="/settings/webhook-destinations"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              manage
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            block outbound deliveries to internal, loopback, and cloud metadata addresses. allow trusted host suffixes for on-prem receivers. admin role required.
          </p>
        </section>

        {/* Webhook egress IPs (firewall allowlist disclosure) */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">webhook egress IPs</span>
            <a
              href="/settings/webhook-egress"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              view
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            source addresses this deployment uses when it dispatches webhooks to your receiver. share with your network team to pin a firewall allowlist instead of opening a support ticket.
          </p>
        </section>

        {/* Webhook HTTPS-only policy */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">webhook transport policy</span>
            <a
              href="/settings/webhook-policy"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              configure
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            require https for every webhook destination in this workspace. blocks plaintext registrations and blocks deliveries to pre-existing http endpoints once enforcement is on.
          </p>
        </section>

        {/* Webhook delivery rate cap */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">webhook delivery rate</span>
            <a
              href="/settings/webhook-delivery-rate"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              configure
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cap outbound deliveries per webhook per minute so a runaway producer cannot exceed the receiver budget. suppressed attempts are still recorded in the delivery log and audit trail.
          </p>
        </section>

        {/* Webhook destination cap */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">webhook destination cap</span>
            <a
              href="/settings/webhook-destination-cap"
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              configure
            </a>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            cap the number of registered outbound destinations this workspace can hold. creates over the cap fail with HTTP 429 and a structured error so operators notice instead of silently growing credential sprawl.
          </p>
        </section>

        {/* Onboarding controls */}
        <section className="panel rounded-[2px] p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="label-xs">onboarding</span>
            <button
              type="button"
              onClick={() => {
                resetOnboarding();
                window.location.href = "/";
              }}
              className="ml-auto border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              replay first-run tour
            </button>
          </div>
          <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
            clears the local checklist and reopens the welcome modal on the landing page.
          </p>
        </section>
      </div>
    </div>
  );
}
