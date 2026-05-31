"use client";

/**
 * Webhook egress IP disclosure.
 *
 * Customers' network teams need to allowlist the source addresses we
 * dispatch webhooks from before they will sign a contract. This page
 * surfaces the deployment's pinned egress list (set via
 * CLAWHUM_WEBHOOK_EGRESS_IPS) so a buyer can copy them straight into
 * their corporate firewall, with a copy-to-clipboard helper and a
 * timestamp that flags drift if the operator rotates IPs.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Copy,
  Warning,
  CheckCircle,
  Globe,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface EgressResponse {
  pinned: boolean;
  addresses: string[];
  updated_at: string;
  note: string;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: EgressResponse }
  | { kind: "error"; message: string };

export default function WebhookEgressPage() {
  const apiKey = useApiKey();
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    const key = getApiKey();
    if (!key) {
      setState({
        kind: "error",
        message: "set an API key on /developers to view egress IPs",
      });
      return;
    }
    try {
      const r = await fetch("/api/v1/webhooks/egress-ips", {
        headers: { "X-API-Key": key, Accept: "application/json" },
        cache: "no-store",
      });
      if (r.status === 401 || r.status === 403) {
        setState({
          kind: "error",
          message: "your API key is not authorised for this workspace",
        });
        return;
      }
      if (!r.ok) {
        setState({
          kind: "error",
          message: `server returned ${r.status}`,
        });
        return;
      }
      const data = (await r.json()) as EgressResponse;
      setState({ kind: "ready", data });
    } catch (e) {
      setState({
        kind: "error",
        message: e instanceof Error ? e.message : "network error",
      });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, apiKey]);

  const copyAll = useCallback(async () => {
    if (state.kind !== "ready" || state.data.addresses.length === 0) return;
    try {
      await navigator.clipboard.writeText(state.data.addresses.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked in iframes/insecure contexts; UI stays usable
    }
  }, [state]);

  return (
    <main className="min-h-dvh px-4 py-8 sm:px-8">
      <div className="mx-auto w-full max-w-3xl space-y-6">
        <header className="flex items-center gap-3">
          <Link
            href="/settings"
            className="border border-[var(--color-line)] p-2 text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            aria-label="back to settings"
          >
            <ArrowLeft size={14} weight="duotone" />
          </Link>
          <div>
            <h1 className="font-mono text-sm uppercase tracking-widest text-[var(--color-phosphor)]">
              webhook egress IPs
            </h1>
            <p className="font-mono text-[10px] text-[var(--color-dim)]">
              source addresses this deployment uses for outbound webhook deliveries
            </p>
          </div>
        </header>

        {state.kind === "loading" && (
          <section className="panel rounded-[2px] p-6">
            <p className="font-mono text-[11px] text-[var(--color-dim)]">loading...</p>
          </section>
        )}

        {state.kind === "error" && (
          <section className="panel rounded-[2px] p-6 space-y-3">
            <div className="flex items-center gap-2 text-[var(--color-warn,#d97706)]">
              <Warning size={14} weight="duotone" />
              <span className="font-mono text-[11px] uppercase tracking-widest">unable to load</span>
            </div>
            <p className="font-mono text-[11px] text-[var(--color-muted)]">{state.message}</p>
            <button
              type="button"
              onClick={load}
              className="border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              retry
            </button>
          </section>
        )}

        {state.kind === "ready" && (
          <>
            <section className="panel rounded-[2px] p-4 space-y-3">
              <div className="flex items-center gap-2">
                <Globe size={14} weight="duotone" className="text-[var(--color-muted)]" />
                <span className="label-xs">status</span>
                <span
                  className={`ml-auto font-mono text-[10px] uppercase tracking-widest ${
                    state.data.pinned
                      ? "text-[var(--color-phosphor)]"
                      : "text-[var(--color-dim)]"
                  }`}
                >
                  {state.data.pinned ? "pinned" : "not pinned"}
                </span>
              </div>
              <p className="font-mono text-[10px] text-[var(--color-dim)] leading-relaxed">
                {state.data.note}
              </p>
              {state.data.updated_at && (
                <p className="font-mono text-[10px] text-[var(--color-dim)]">
                  last operator update:{" "}
                  <span className="text-[var(--color-muted)]">{state.data.updated_at}</span>
                </p>
              )}
            </section>

            <section className="panel rounded-[2px] p-4 space-y-3">
              <div className="flex items-center gap-2">
                <span className="label-xs">addresses</span>
                {state.data.addresses.length > 0 && (
                  <button
                    type="button"
                    onClick={copyAll}
                    className="ml-auto inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
                  >
                    {copied ? (
                      <>
                        <CheckCircle size={12} weight="duotone" /> copied
                      </>
                    ) : (
                      <>
                        <Copy size={12} weight="duotone" /> copy all
                      </>
                    )}
                  </button>
                )}
              </div>
              {state.data.addresses.length === 0 ? (
                <p className="font-mono text-[11px] text-[var(--color-dim)] leading-relaxed">
                  no egress addresses configured. set CLAWHUM_WEBHOOK_EGRESS_IPS on the API service to a comma separated list of IPv4 or IPv6 addresses or CIDRs.
                </p>
              ) : (
                <ul className="space-y-1">
                  {state.data.addresses.map((addr) => (
                    <li
                      key={addr}
                      className="font-mono text-[12px] text-[var(--color-phosphor)] border border-[var(--color-line)] px-3 py-2 break-all"
                    >
                      {addr}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel rounded-[2px] p-4 space-y-2">
              <span className="label-xs">curl</span>
              <pre className="overflow-x-auto font-mono text-[10px] text-[var(--color-muted)] leading-relaxed">
{`curl -H "X-API-Key: $CLAWHUM_API_KEY" \\
  ${typeof window !== "undefined" ? window.location.origin : "https://your-clawhum"}/api/v1/webhooks/egress-ips`}
              </pre>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
