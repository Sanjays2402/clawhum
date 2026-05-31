"use client";

/**
 * Workspace data residency pin.
 *
 * Owners pick the region this workspace's data must live in. The
 * backend enforces the pin on every mutating request via the residency
 * middleware, so the moment a request hits the wrong region node it is
 * rejected with 451 and the dashboard can show the violation. MFA is
 * required to save.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Globe,
  ArrowLeft,
  Warning,
  CheckCircle,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface ResidencyOut {
  tenant_id: string;
  region: string;
  enforce: boolean;
  updated_at: number;
  updated_by: string;
}

interface ReadResp {
  residency: ResidencyOut;
  node_region: string;
  enforcement: boolean;
  available_regions: string[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ReadResp }
  | { kind: "error"; status: number; message: string };

const REGION_LABEL: Record<string, string> = {
  us: "United States",
  eu: "European Economic Area",
  apac: "Asia Pacific",
  unset: "No pin",
};

function authHeaders(mfa?: string): Record<string, string> {
  const k = getApiKey();
  const h: Record<string, string> = {};
  if (k) h["X-API-Key"] = k;
  if (mfa) h["X-MFA-Code"] = mfa;
  return h;
}

function formatTs(ts: number): string {
  if (!ts) return "Never";
  return new Date(ts * 1000).toLocaleString();
}

export default function ResidencyPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [region, setRegion] = useState("unset");
  const [enforce, setEnforce] = useState(false);
  const [mfa, setMfa] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/residency", { headers: authHeaders() });
      if (!r.ok) {
        const body = await r.text();
        setState({
          kind: "error",
          status: r.status,
          message: body || r.statusText,
        });
        return;
      }
      const data = (await r.json()) as ReadResp;
      setState({ kind: "ready", data });
      setRegion(data.residency.region);
      setEnforce(data.residency.enforce);
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

  async function save() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const r = await fetch("/api/residency", {
        method: "PUT",
        headers: { ...authHeaders(mfa), "Content-Type": "application/json" },
        body: JSON.stringify({ region, enforce }),
      });
      if (!r.ok) {
        const body = await r.text();
        if (
          r.status === 401 &&
          r.headers.get("www-authenticate")?.toLowerCase().includes("mfa")
        ) {
          setSaveError("MFA code required. Enter your TOTP code below.");
        } else {
          setSaveError(body || r.statusText);
        }
        return;
      }
      setSaved(true);
      setMfa("");
      await refresh();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const ready = state.kind === "ready" ? state.data : null;
  const nodeRegion = ready?.node_region ?? "unset";
  const mismatch =
    ready !== null &&
    enforce &&
    region !== "unset" &&
    nodeRegion !== "unset" &&
    region !== nodeRegion;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        <ArrowLeft size={14} weight="duotone" />
        Settings
      </Link>
      <div className="mt-3 flex items-start gap-3">
        <div className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">
          <Globe size={20} weight="duotone" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Data residency
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Pin this workspace to a region. When enforcement is on,
            mutating requests against a node in a different region are
            rejected with HTTP 451.
          </p>
        </div>
      </div>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-3">
          <div className="h-24 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
          <div className="h-40 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          <Warning size={18} weight="duotone" className="mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">
              Could not load residency ({state.status})
            </div>
            <div className="mt-1 break-all">{state.message}</div>
            {state.status === 401 && (
              <div className="mt-2">
                Set your API key in{" "}
                <Link href="/settings" className="underline">
                  Settings
                </Link>
                . Admin role required.
              </div>
            )}
          </div>
        </div>
      )}

      {ready && (
        <>
          <section className="mt-8 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
                Current pin
              </h2>
              <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium dark:bg-zinc-900">
                {REGION_LABEL[ready.residency.region] ?? ready.residency.region}
              </span>
            </div>
            <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-zinc-500">Region</dt>
                <dd className="mt-1 text-xl font-semibold">
                  {ready.residency.region}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-500">Enforcement</dt>
                <dd className="mt-1 text-xl font-semibold">
                  {ready.residency.enforce ? "On" : "Off"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-500">This node</dt>
                <dd className="mt-1 text-xl font-semibold">{nodeRegion}</dd>
              </div>
            </dl>
            <div className="mt-3 text-xs text-zinc-500">
              Last updated {formatTs(ready.residency.updated_at)}
              {ready.residency.updated_by
                ? ` by ${ready.residency.updated_by}`
                : null}
              . Global enforcement switch is{" "}
              <span className="font-medium">
                {ready.enforcement ? "on" : "off"}
              </span>
              .
            </div>
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Change pin
            </h2>
            <fieldset className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-4">
              <legend className="sr-only">Region</legend>
              {ready.available_regions.map((r) => (
                <label
                  key={r}
                  className={`cursor-pointer rounded-md border px-3 py-2 text-left text-sm transition ${
                    region === r
                      ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                      : "border-zinc-200 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600"
                  }`}
                >
                  <input
                    type="radio"
                    name="region"
                    value={r}
                    checked={region === r}
                    onChange={() => setRegion(r)}
                    className="sr-only"
                  />
                  <div className="font-medium">{r}</div>
                  <div className="mt-1 text-xs opacity-75">
                    {REGION_LABEL[r] ?? r}
                  </div>
                </label>
              ))}
            </fieldset>

            <label className="mt-5 flex items-start gap-3 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
              <input
                type="checkbox"
                checked={enforce}
                disabled={region === "unset"}
                onChange={(e) => setEnforce(e.target.checked)}
                className="mt-0.5"
              />
              <span className="text-sm">
                <span className="font-medium">Enforce this region.</span>{" "}
                <span className="text-zinc-500">
                  When on, mutating requests to a node outside{" "}
                  <span className="font-medium">{region}</span> return HTTP
                  451. Reads are still allowed in any region.
                </span>
              </span>
            </label>

            {mismatch && (
              <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                <Warning size={16} weight="duotone" className="mt-0.5 shrink-0" />
                <div>
                  Saving will immediately block this dashboard's mutating
                  calls from this node. You are on a{" "}
                  <span className="font-medium">{nodeRegion}</span> node and
                  are about to pin to{" "}
                  <span className="font-medium">{region}</span>. Reads will
                  still work; writes must be made against a{" "}
                  <span className="font-medium">{region}</span> node.
                </div>
              </div>
            )}

            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
              <label className="block text-sm">
                <span className="flex items-center gap-1 text-zinc-700 dark:text-zinc-300">
                  <LockKey size={14} weight="duotone" />
                  MFA code
                </span>
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={mfa}
                  onChange={(e) =>
                    setMfa(e.target.value.replace(/\D/g, "").slice(0, 6))
                  }
                  placeholder="123456"
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm tabular-nums focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
                />
              </label>
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                {saving ? "Saving" : "Save residency"}
              </button>
            </div>

            {saveError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                <Warning size={16} weight="duotone" className="mt-0.5 shrink-0" />
                <div className="break-all">{saveError}</div>
              </div>
            )}
            {saved && !saveError && (
              <div className="mt-3 flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
                <CheckCircle size={16} weight="duotone" />
                Saved. Pin is active on the next request.
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
