"use client";

/**
 * Workspace admin console.
 *
 * One screen that surfaces the things an enterprise buyer asks for
 * during procurement: who runs this workspace, which API keys exist,
 * what was the recent admin activity, are SSO and MFA on, where does
 * traffic stand against quota. Every section is a live read from the
 * existing backend routes; nothing is invented here. Cards link out to
 * the dedicated settings sub-pages for mutations so this stays a true
 * overview rather than another half-built form.
 */

import Link from "next/link";
import useSWR from "swr";
import {
  ShieldCheck,
  Key,
  Users,
  ListMagnifyingGlass,
  Gauge,
  Lock,
  Broadcast,
  ShieldStar,
  ShieldWarning,
  ArrowSquareOut,
  Warning,
  Pulse,
  GlobeHemisphereWest,
  HardDrives,
  CloudArrowUp,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey } from "@/lib/apiKey";

interface Me {
  tenant_id: string;
  key_name: string;
  roles: string[];
  rate_limit_per_minute: number;
  auth_mode: "open" | "key";
  masked_key: string;
  sso_configured: boolean;
  sso_enforced: boolean;
  sso_provider: string;
  sso_email_domain: string;
}

interface KeyView {
  id: string;
  name: string;
  roles: string[];
  rpm: number;
  created_at: number;
  last_used_at: number;
  secret_hint: string;
  expires_at: number;
  expired: boolean;
  scopes: string[];
  effective_scopes: string[];
}

interface MemberView {
  id: string;
  email: string;
  role: string;
  status: "invited" | "active" | "revoked";
  invited_by: string;
  invited_at: number;
  accepted_at: number;
  invite_expires_at: number;
}

interface MemberList {
  members: MemberView[];
  counts: Record<string, number>;
}

interface AuditEvent {
  ts: number;
  actor: string;
  api_key_name: string | null;
  tenant_id: string | null;
  roles: string[];
  method: string;
  path: string;
  status: number;
  dry_run?: boolean;
}

interface AuditList {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  truncated: boolean;
}

interface UsageResponse {
  minute?: number;
  day?: number;
  month?: number;
  rpm_quota?: number;
  daily_quota?: number;
  [k: string]: unknown;
}

interface MfaStatus {
  enabled: boolean;
  verified?: boolean;
  enrolled_at?: number;
}

interface Quota {
  plan: string;
  rpm: number;
  daily: number;
}

async function authedFetcher<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const k = getApiKey();
  if (k) headers["X-API-Key"] = k;
  const r = await fetch(path, { headers, cache: "no-store" });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    const err = new Error(`${r.status} ${r.statusText} ${text}`.trim());
    (err as Error & { status?: number }).status = r.status;
    throw err;
  }
  return r.json() as Promise<T>;
}

function fmtTs(ts: number): string {
  if (!ts || ts <= 0) return "never";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function fmtRel(ts: number): string {
  if (!ts || ts <= 0) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function StatusDot({ on, warn }: { on: boolean; warn?: boolean }) {
  const bg = on
    ? "bg-emerald-500"
    : warn
    ? "bg-amber-500"
    : "bg-[var(--color-dim)]";
  return (
    <span
      aria-hidden
      className={`inline-block size-2 rounded-full ${bg}`}
    />
  );
}

function Card({
  title,
  icon,
  href,
  hrefLabel,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  href?: string;
  hrefLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] p-4 flex flex-col gap-3 min-h-[180px]">
      <header className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
          <span aria-hidden>{icon}</span>
          {title}
        </h2>
        {href ? (
          <Link
            href={href}
            className="flex items-center gap-1 text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)] hover:text-[var(--color-fg)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-500 rounded-sm"
          >
            {hrefLabel || "open"}
            <ArrowSquareOut size={12} weight="duotone" />
          </Link>
        ) : null}
      </header>
      <div className="flex-1">{children}</div>
    </section>
  );
}

function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-3 rounded bg-[var(--color-line)] animate-pulse"
          style={{ width: `${60 + ((i * 13) % 30)}%` }}
        />
      ))}
    </div>
  );
}

function ErrBlock({ err }: { err: Error & { status?: number } }) {
  const forbid = err.status === 403;
  const auth = err.status === 401;
  return (
    <div className="flex items-start gap-2 text-xs text-amber-400">
      <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
      <div>
        {auth
          ? "Set an API key in settings to view this."
          : forbid
          ? "Admin role required."
          : err.message}
      </div>
    </div>
  );
}

export default function AdminConsolePage() {
  const me = useSWR<Me, Error>("/api/me", authedFetcher, {
    refreshInterval: 60_000,
  });
  const members = useSWR<MemberList, Error>("/api/members", authedFetcher, {
    refreshInterval: 60_000,
  });
  const keys = useSWR<KeyView[], Error>("/api/keys", authedFetcher, {
    refreshInterval: 60_000,
  });
  const audit = useSWR<AuditList, Error>(
    "/api/audit?limit=6",
    authedFetcher,
    { refreshInterval: 30_000 },
  );
  const usage = useSWR<UsageResponse, Error>("/api/usage", authedFetcher, {
    refreshInterval: 30_000,
  });
  const mfa = useSWR<MfaStatus, Error>("/api/mfa/status", authedFetcher, {
    refreshInterval: 60_000,
  });
  const quota = useSWR<Quota, Error>("/api/quotas", authedFetcher, {
    refreshInterval: 60_000,
  });

  const meErr = me.error as (Error & { status?: number }) | undefined;
  const isOpen = me.data?.auth_mode === "open";

  const activeMembers = members.data?.counts?.active ?? 0;
  const invitedMembers = members.data?.counts?.invited ?? 0;
  const activeKeys = (keys.data || []).filter((k) => !k.expired).length;
  const expiringSoon = (keys.data || []).filter(
    (k) =>
      !k.expired &&
      k.expires_at > 0 &&
      k.expires_at - Date.now() / 1000 < 7 * 86400,
  ).length;

  return (
    <div className="px-4 md:px-8 py-6 max-w-[1400px] mx-auto space-y-6">
      <header className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Admin console
          </h1>
          <p className="text-xs text-[var(--color-dim)] mt-1">
            One view of identity, access, audit and usage for this workspace.
          </p>
        </div>
        {me.data ? (
          <div className="flex items-center gap-3 text-[11px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
            <span>workspace</span>
            <code className="text-[var(--color-fg)]">{me.data.tenant_id}</code>
            {isOpen ? (
              <span className="text-amber-400">dev mode</span>
            ) : (
              <span className="flex items-center gap-1">
                <StatusDot on /> live
              </span>
            )}
          </div>
        ) : null}
      </header>

      {isOpen ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-amber-200 flex items-start gap-2">
          <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
          <div>
            Auth is in open mode. Member roles, audit attribution and quotas
            are not enforced. Set <code>CLAWHUM_API_KEYS</code> on the API to
            switch to multi-tenant mode.
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
        <Card
          title="identity"
          icon={<ShieldCheck size={14} weight="duotone" />}
          href="/settings"
          hrefLabel="manage"
        >
          {me.isLoading ? (
            <Skeleton rows={3} />
          ) : meErr ? (
            <ErrBlock err={meErr} />
          ) : me.data ? (
            <dl className="text-xs space-y-1.5">
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">key</dt>
                <dd className="font-mono">{me.data.key_name}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">secret</dt>
                <dd className="font-mono">{me.data.masked_key}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">roles</dt>
                <dd className="font-mono">
                  {me.data.roles.join(", ") || "none"}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">rpm</dt>
                <dd className="font-mono">{me.data.rate_limit_per_minute}</dd>
              </div>
            </dl>
          ) : null}
        </Card>

        <Card
          title="members"
          icon={<Users size={14} weight="duotone" />}
          href="/settings/members"
          hrefLabel="manage"
        >
          {members.isLoading ? (
            <Skeleton rows={2} />
          ) : members.error ? (
            <ErrBlock err={members.error as Error & { status?: number }} />
          ) : members.data ? (
            <div className="space-y-3">
              <div className="flex items-baseline gap-4">
                <div>
                  <div className="text-2xl font-semibold tabular-nums">
                    {activeMembers}
                  </div>
                  <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
                    active
                  </div>
                </div>
                <div>
                  <div className="text-2xl font-semibold tabular-nums text-amber-300">
                    {invitedMembers}
                  </div>
                  <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
                    invited
                  </div>
                </div>
              </div>
              <ul className="text-xs space-y-1 max-h-24 overflow-auto">
                {(members.data.members || []).slice(0, 4).map((m) => (
                  <li
                    key={m.id}
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="truncate">{m.email}</span>
                    <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                      {m.role}
                    </span>
                  </li>
                ))}
                {(members.data.members || []).length === 0 ? (
                  <li className="text-[var(--color-dim)]">
                    No members yet. Invite one from the members page.
                  </li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </Card>

        <Card
          title="api keys"
          icon={<Key size={14} weight="duotone" />}
          href="/settings/keys"
          hrefLabel="manage"
        >
          {keys.isLoading ? (
            <Skeleton rows={2} />
          ) : keys.error ? (
            <ErrBlock err={keys.error as Error & { status?: number }} />
          ) : keys.data ? (
            <div className="space-y-3">
              <div className="flex items-baseline gap-4">
                <div>
                  <div className="text-2xl font-semibold tabular-nums">
                    {activeKeys}
                  </div>
                  <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
                    active tokens
                  </div>
                </div>
                {expiringSoon > 0 ? (
                  <div>
                    <div className="text-2xl font-semibold tabular-nums text-amber-300">
                      {expiringSoon}
                    </div>
                    <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
                      expire {"<"} 7d
                    </div>
                  </div>
                ) : null}
              </div>
              <ul className="text-xs space-y-1 max-h-24 overflow-auto">
                {(keys.data || []).slice(0, 4).map((k) => (
                  <li
                    key={k.id}
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="truncate font-mono">{k.name}</span>
                    <span className="text-[10px] text-[var(--color-dim)]">
                      used {fmtRel(k.last_used_at)}
                    </span>
                  </li>
                ))}
                {(keys.data || []).length === 0 ? (
                  <li className="text-[var(--color-dim)]">
                    No personal access tokens minted.
                  </li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </Card>

        <Card
          title="usage"
          icon={<Gauge size={14} weight="duotone" />}
          href="/usage"
          hrefLabel="meter"
        >
          {usage.isLoading ? (
            <Skeleton rows={3} />
          ) : usage.error ? (
            <ErrBlock err={usage.error as Error & { status?: number }} />
          ) : usage.data ? (
            <dl className="text-xs space-y-1.5">
              <div className="flex justify-between">
                <dt className="text-[var(--color-dim)]">last minute</dt>
                <dd className="font-mono tabular-nums">
                  {Number(usage.data.minute ?? 0)}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--color-dim)]">today</dt>
                <dd className="font-mono tabular-nums">
                  {Number(usage.data.day ?? 0)}
                  {quota.data?.daily ? ` / ${quota.data.daily}` : ""}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--color-dim)]">this month</dt>
                <dd className="font-mono tabular-nums">
                  {Number(usage.data.month ?? 0)}
                </dd>
              </div>
              {quota.data ? (
                <div className="flex justify-between pt-1 border-t border-[var(--color-line)]">
                  <dt className="text-[var(--color-dim)]">plan</dt>
                  <dd className="font-mono uppercase tracking-widest">
                    {quota.data.plan}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </Card>

        <Card
          title="sso"
          icon={<GlobeHemisphereWest size={14} weight="duotone" />}
          href="/settings/sso"
          hrefLabel="configure"
        >
          {me.isLoading ? (
            <Skeleton rows={2} />
          ) : meErr ? (
            <ErrBlock err={meErr} />
          ) : me.data ? (
            <dl className="text-xs space-y-1.5">
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">status</dt>
                <dd className="flex items-center gap-1.5">
                  <StatusDot
                    on={me.data.sso_configured}
                    warn={!me.data.sso_enforced && me.data.sso_configured}
                  />
                  <span>
                    {me.data.sso_configured
                      ? me.data.sso_enforced
                        ? "enforced"
                        : "configured"
                      : "not set up"}
                  </span>
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">provider</dt>
                <dd className="font-mono">
                  {me.data.sso_provider || "none"}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--color-dim)]">domain</dt>
                <dd className="font-mono truncate max-w-[10rem]">
                  {me.data.sso_email_domain || "none"}
                </dd>
              </div>
            </dl>
          ) : null}
        </Card>

        <Card
          title="mfa"
          icon={<Lock size={14} weight="duotone" />}
          href="/settings/security"
          hrefLabel="manage"
        >
          {mfa.isLoading ? (
            <Skeleton rows={2} />
          ) : mfa.error ? (
            <ErrBlock err={mfa.error as Error & { status?: number }} />
          ) : mfa.data ? (
            <div className="space-y-2 text-xs">
              <div className="flex items-center gap-2">
                <StatusDot on={Boolean(mfa.data.enabled)} warn />
                <span>
                  {mfa.data.enabled
                    ? "TOTP enrolled"
                    : "TOTP not enrolled"}
                </span>
              </div>
              {mfa.data.enrolled_at ? (
                <div className="text-[var(--color-dim)]">
                  since {fmtTs(mfa.data.enrolled_at)}
                </div>
              ) : (
                <div className="text-[var(--color-dim)]">
                  Required for destructive admin actions.
                </div>
              )}
            </div>
          ) : null}
        </Card>

        <Card
          title="mfa lockouts"
          icon={<ShieldWarning size={14} weight="duotone" />}
          href="/admin/mfa-lockouts"
          hrefLabel="review"
        >
          <p className="text-xs text-[var(--color-dim)]">
            Actors whose recent MFA submissions have tripped the brute
            force cooldown. Clearing a lock is logged to the audit
            chain so any override is attributable.
          </p>
        </Card>

        <Card
          title="ip allowlist"
          icon={<ShieldStar size={14} weight="duotone" />}
          href="/settings/ip-allowlist"
          hrefLabel="manage"
        >
          <p className="text-xs text-[var(--color-dim)]">
            Restrict which source IPs may call the API and dashboard. Empty
            allowlist means open from any IP.
          </p>
        </Card>

        <Card
          title="webhooks"
          icon={<Broadcast size={14} weight="duotone" />}
          href="/webhooks"
          hrefLabel="endpoints"
        >
          <p className="text-xs text-[var(--color-dim)]">
            HMAC signed outbound deliveries with retries and per
            workspace destination allowlist.
          </p>
        </Card>

        <Card
          title="scim provisioning"
          icon={<CloudArrowUp size={14} weight="duotone" />}
          href="/settings/scim"
          hrefLabel="manage"
        >
          <p className="text-xs text-[var(--color-dim)]">
            SCIM 2.0 endpoint for Okta, Azure AD, and Google Workspace.
            Mint one bearer token per workspace and your identity
            provider can sync joiners and leavers automatically.
          </p>
        </Card>

        <Card
          title="audit forwarding"
          icon={<Broadcast size={14} weight="duotone" />}
          href="/admin/audit-forwarding"
          hrefLabel="configure"
        >
          <p className="text-xs text-[var(--color-dim)]">
            Stream this workspace audit log to your own SIEM or HTTPS
            collector. Events are signed with HMAC-SHA256; failed
            deliveries retry with backoff and can be replayed.
          </p>
        </Card>

        <Card
          title="data processing agreement"
          icon={<ShieldCheck size={14} weight="duotone" />}
          href="/admin/dpa"
          hrefLabel="review"
        >
          <p className="text-xs text-[var(--color-dim)]">
            Record the workspace acceptance of the vendor DPA. Required
            by most enterprise procurement and GDPR Article 28 reviews.
            Admin only, MFA gated, and written to the audit chain.
          </p>
        </Card>

        <Card
          title="monthly budget cap"
          icon={<Gauge size={14} weight="duotone" />}
          href="/admin/budget"
          hrefLabel="configure"
        >
          <p className="text-xs text-[var(--color-dim)]">
            Hard ceiling on chargeable requests for this workspace over
            a rolling 30 day window. Bounds the month while rate limits
            bound the rate. Returns HTTP 402 past the cap or runs in
            audit only mode during rollout. Admin only, MFA gated.
          </p>
        </Card>

        <Card
          title="request body cap"
          icon={<HardDrives size={14} weight="duotone" />}
          href="/admin/body-size"
          hrefLabel="configure"
        >
          <p className="text-xs text-[var(--color-dim)]">
            Workspace ceiling on the size of any inbound request body.
            Oversized payloads return HTTP 413 before the route runs,
            so they never touch the worker or count against the
            monthly quota. Admin only, MFA gated.
          </p>
        </Card>

        <Card
          title="recent activity"
          icon={<ListMagnifyingGlass size={14} weight="duotone" />}
          href="/settings/audit"
          hrefLabel="full log"
        >
          {audit.isLoading ? (
            <Skeleton rows={4} />
          ) : audit.error ? (
            <ErrBlock err={audit.error as Error & { status?: number }} />
          ) : audit.data ? (
            <ul className="text-[11px] font-mono space-y-1 max-h-40 overflow-auto">
              {(audit.data.items || []).map((e, i) => (
                <li
                  key={`${e.ts}-${i}`}
                  className="grid grid-cols-[auto_auto_1fr_auto] gap-2 items-center"
                >
                  <span
                    className={
                      e.status >= 500
                        ? "text-rose-400"
                        : e.status >= 400
                        ? "text-amber-400"
                        : "text-emerald-400"
                    }
                  >
                    {e.status}
                  </span>
                  <span className="text-[var(--color-dim)]">{e.method}</span>
                  <span className="truncate">{e.path}</span>
                  <span className="text-[var(--color-dim)] text-[10px]">
                    {fmtRel(e.ts)}
                  </span>
                </li>
              ))}
              {(audit.data.items || []).length === 0 ? (
                <li className="text-[var(--color-dim)]">
                  No audit events recorded yet.
                </li>
              ) : null}
            </ul>
          ) : null}
        </Card>
      </div>

      <footer className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)] flex items-center gap-2 pt-2 border-t border-[var(--color-line)]">
        <Pulse size={12} weight="duotone" />
        <span>auto refresh on read endpoints</span>
      </footer>
    </div>
  );
}
