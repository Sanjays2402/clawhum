"use client";

/**
 * Workspace webhook destination policy.
 *
 * Outbound webhook deliveries are blocked by default when the
 * destination resolves to a loopback, link local, multicast, or
 * RFC1918 address; cloud metadata endpoints are denied globally.
 * Admins can add host suffixes here to deliver to receivers on a
 * private network they control (eg ``acme.internal`` matches
 * ``api.acme.internal`` and any deeper subdomain).
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldWarning,
  Plus,
  Trash,
  Warning,
  ArrowLeft,
  Globe,
  CheckCircle,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface AllowlistResponse {
  tenant_id: string;
  hosts: string[];
  block_private_ips: boolean;
  note: string;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: AllowlistResponse }
  | { kind: "error"; message: string };

const HOST_PATTERN = /^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/i;

export default function WebhookDestinationsPage() {
  const apiKey = useApiKey();
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [statusKind, setStatusKind] = useState<"ok" | "err">("ok");

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const key = getApiKey();
      const headers: Record<string, string> = { Accept: "application/json" };
      if (key) headers["X-API-Key"] = key;
      const r = await fetch("/api/v1/webhooks/destination-allowlist", {
        headers,
        cache: "no-store",
      });
      if (r.status === 401) {
        setState({ kind: "error", message: "Sign in with a workspace API key to view this page." });
        return;
      }
      if (!r.ok) {
        setState({ kind: "error", message: `Server returned ${r.status}` });
        return;
      }
      const data = (await r.json()) as AllowlistResponse;
      setState({ kind: "ready", data });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Network error" });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, apiKey]);

  async function save(nextHosts: string[]) {
    setBusy(true);
    setStatus(null);
    try {
      const key = getApiKey();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (key) headers["X-API-Key"] = key;
      const r = await fetch("/api/v1/webhooks/destination-allowlist", {
        method: "PUT",
        headers,
        body: JSON.stringify({ hosts: nextHosts }),
      });
      if (r.status === 403) {
        setStatusKind("err");
        setStatus("This action needs an admin API key.");
        return;
      }
      if (!r.ok) {
        setStatusKind("err");
        setStatus(`Save failed (${r.status})`);
        return;
      }
      const data = (await r.json()) as AllowlistResponse;
      setState({ kind: "ready", data });
      setStatusKind("ok");
      setStatus("Saved.");
    } catch (e) {
      setStatusKind("err");
      setStatus(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusy(false);
    }
  }

  function addHost() {
    const value = draft.trim().toLowerCase().replace(/^\.+|\.+$/g, "");
    if (!value) return;
    if (!HOST_PATTERN.test(value)) {
      setStatusKind("err");
      setStatus("Use a bare hostname like acme.internal (no scheme, no path).");
      return;
    }
    if (state.kind !== "ready") return;
    if (state.data.hosts.includes(value)) {
      setStatusKind("err");
      setStatus("That host suffix is already in the list.");
      return;
    }
    const next = [...state.data.hosts, value].sort();
    setDraft("");
    save(next);
  }

  function removeHost(host: string) {
    if (state.kind !== "ready") return;
    const next = state.data.hosts.filter((h) => h !== host);
    save(next);
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-xs uppercase tracking-widest text-[var(--color-dim)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft size={14} weight="duotone" />
        Settings
      </Link>

      <header className="mt-4 flex items-start gap-3">
        <div className="rounded-md border border-[var(--color-line)] p-2 text-[var(--color-text)]">
          <ShieldWarning size={22} weight="duotone" />
        </div>
        <div>
          <h1 className="text-lg font-medium">Webhook destinations</h1>
          <p className="mt-1 text-sm text-[var(--color-dim)]">
            Control which hosts this workspace can deliver webhooks to.
          </p>
        </div>
      </header>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-3" aria-live="polite">
          <div className="h-10 w-full animate-pulse border border-[var(--color-line)] bg-[var(--color-bg)]" />
          <div className="h-10 w-3/4 animate-pulse border border-[var(--color-line)] bg-[var(--color-bg)]" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-2 border border-[var(--color-line)] p-3 text-sm text-[var(--color-text)]">
          <Warning size={16} weight="duotone" className="mt-0.5 shrink-0" />
          <span>{state.message}</span>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <section className="mt-6 border border-[var(--color-line)] p-4">
            <div className="flex items-center gap-2">
              {state.data.block_private_ips ? (
                <>
                  <CheckCircle size={16} weight="duotone" />
                  <span className="text-sm">
                    SSRF protection is on. Internal addresses are blocked by default.
                  </span>
                </>
              ) : (
                <>
                  <Warning size={16} weight="duotone" />
                  <span className="text-sm">
                    SSRF protection is currently disabled on this deployment.
                    Re-enable <code className="font-mono text-xs">CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS</code> in production.
                  </span>
                </>
              )}
            </div>
            <p className="mt-2 text-xs text-[var(--color-dim)]">{state.data.note}</p>
          </section>

          <section className="mt-6">
            <h2 className="text-xs uppercase tracking-widest text-[var(--color-dim)]">
              Trusted host suffixes
            </h2>

            <div className="mt-3 flex gap-2">
              <input
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addHost();
                  }
                }}
                placeholder="acme.internal"
                aria-label="Host suffix to allow"
                className="flex-1 border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 font-mono text-sm text-[var(--color-text)] focus:border-[var(--color-text)] focus:outline-none"
                disabled={busy}
              />
              <button
                type="button"
                onClick={addHost}
                disabled={busy || !draft.trim()}
                className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-2 text-xs uppercase tracking-widest text-[var(--color-text)] hover:bg-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus size={14} weight="duotone" />
                Add
              </button>
            </div>

            {status && (
              <p
                role="status"
                className={`mt-2 text-xs ${statusKind === "ok" ? "text-[var(--color-text)]" : "text-amber-700 dark:text-amber-400"}`}
              >
                {status}
              </p>
            )}

            <div className="mt-4 border border-[var(--color-line)]">
              {state.data.hosts.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
                  <Globe size={28} weight="duotone" className="text-[var(--color-dim)]" />
                  <p className="text-sm text-[var(--color-text)]">No trusted hosts yet.</p>
                  <p className="text-xs text-[var(--color-dim)]">
                    Only public destinations can receive deliveries until you add one.
                  </p>
                </div>
              ) : (
                <ul>
                  {state.data.hosts.map((host) => (
                    <li
                      key={host}
                      className="flex items-center justify-between border-b border-[var(--color-line)] px-3 py-2 last:border-b-0"
                    >
                      <span className="font-mono text-sm text-[var(--color-text)] break-all">
                        {host}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeHost(host)}
                        disabled={busy}
                        aria-label={`Remove ${host}`}
                        className="inline-flex items-center gap-1 border border-transparent px-2 py-1 text-xs text-[var(--color-dim)] hover:border-[var(--color-line)] hover:text-[var(--color-text)] disabled:opacity-50"
                      >
                        <Trash size={14} weight="duotone" />
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="mt-8 border border-[var(--color-line)] p-4">
            <h3 className="text-xs uppercase tracking-widest text-[var(--color-dim)]">
              How matching works
            </h3>
            <ul className="mt-3 space-y-2 text-sm text-[var(--color-text)]">
              <li className="flex gap-2">
                <span className="text-[var(--color-dim)]">{">"}</span>
                Suffix match: <code className="font-mono text-xs">acme.internal</code> covers <code className="font-mono text-xs">acme.internal</code> and any subdomain.
              </li>
              <li className="flex gap-2">
                <span className="text-[var(--color-dim)]">{">"}</span>
                Cloud metadata hosts stay denied even if you list them.
              </li>
              <li className="flex gap-2">
                <span className="text-[var(--color-dim)]">{">"}</span>
                Every delivery re-resolves DNS, so a host that later points at an internal IP is still blocked.
              </li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
