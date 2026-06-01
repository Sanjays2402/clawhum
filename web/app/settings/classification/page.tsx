"use client";

/**
 * Workspace data classification.
 *
 * Admin sets the sensitivity level for this workspace's data so the
 * platform can apply consistent handling and procurement reviewers
 * have a single, audited place to confirm the contractual label.
 * Setting the level to "restricted" forces the workspace-wide export
 * endpoint to require an explicit X-Classification-Ack header so
 * bulk exports are an intentional act, not a slip.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  ArrowLeft,
  Warning,
  CheckCircle,
  LockKey,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface ClassificationOut {
  tenant_id: string;
  level: string;
  label: string;
  handling_contact: string;
  updated_at: number;
  updated_by: string;
}

interface ReadResp {
  classification: ClassificationOut;
  available_levels: string[];
  requires_ack: boolean;
  ack_header: string;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ReadResp }
  | { kind: "error"; status: number; message: string };

const LEVEL_DESCRIPTION: Record<string, string> = {
  public: "No confidentiality risk. Safe to share without restriction.",
  internal: "Default for typical SaaS use. Restricted to workspace members.",
  confidential: "Business sensitive. Exports are labeled and audited.",
  restricted:
    "Highly sensitive or regulated. Bulk export requires per request acknowledgment.",
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

export default function ClassificationPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [level, setLevel] = useState("internal");
  const [label, setLabel] = useState("");
  const [contact, setContact] = useState("");
  const [mfa, setMfa] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch("/api/classification", { headers: authHeaders() });
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
      setLevel(data.classification.level);
      setLabel(data.classification.label);
      setContact(data.classification.handling_contact);
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
      const r = await fetch("/api/classification", {
        method: "PUT",
        headers: { ...authHeaders(mfa), "Content-Type": "application/json" },
        body: JSON.stringify({
          level,
          label,
          handling_contact: contact,
        }),
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
  const willRequireAck = level === "restricted";

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
          <ShieldCheck size={20} weight="duotone" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Data classification
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Declare the sensitivity of the data this workspace holds.
            The label is surfaced on every export and recorded in the
            audit log so security reviewers have one place to confirm
            handling expectations.
          </p>
        </div>
      </div>

      {state.kind === "loading" && (
        <div className="mt-8 space-y-3">
          <div className="h-24 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
          <div className="h-56 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-8 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          <Warning size={18} weight="duotone" className="mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">
              Could not load classification ({state.status})
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
                Current label
              </h2>
              <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium dark:bg-zinc-900">
                {ready.classification.level}
              </span>
            </div>
            <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-zinc-500">Level</dt>
                <dd className="mt-1 text-xl font-semibold capitalize">
                  {ready.classification.level}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-500">Bulk export ack</dt>
                <dd className="mt-1 text-xl font-semibold">
                  {ready.requires_ack ? "Required" : "Not required"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-500">Handling contact</dt>
                <dd className="mt-1 truncate text-sm">
                  {ready.classification.handling_contact || (
                    <span className="text-zinc-400">None set</span>
                  )}
                </dd>
              </div>
            </dl>
            {ready.classification.label && (
              <div className="mt-3 rounded-md bg-zinc-50 px-3 py-2 text-sm dark:bg-zinc-900">
                <span className="text-zinc-500">Label:</span>{" "}
                {ready.classification.label}
              </div>
            )}
            <div className="mt-3 text-xs text-zinc-500">
              Last updated {formatTs(ready.classification.updated_at)}
              {ready.classification.updated_by
                ? ` by ${ready.classification.updated_by}`
                : null}
              .
            </div>
            {ready.requires_ack && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                <Warning size={14} weight="duotone" className="mt-0.5 shrink-0" />
                <div>
                  Bulk export calls must include{" "}
                  <code className="rounded bg-amber-100 px-1 py-0.5 dark:bg-amber-900">
                    {ready.ack_header}: {ready.classification.level}
                  </code>{" "}
                  or the API returns 428 Precondition Required.
                </div>
              </div>
            )}
          </section>

          <section className="mt-6 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Change classification
            </h2>
            <fieldset className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <legend className="sr-only">Level</legend>
              {ready.available_levels.map((lv) => (
                <label
                  key={lv}
                  className={`cursor-pointer rounded-md border px-3 py-2 text-left text-sm transition ${
                    level === lv
                      ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                      : "border-zinc-200 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-600"
                  }`}
                >
                  <input
                    type="radio"
                    name="level"
                    value={lv}
                    checked={level === lv}
                    onChange={() => setLevel(lv)}
                    className="sr-only"
                  />
                  <div className="font-medium capitalize">{lv}</div>
                  <div className="mt-1 text-xs opacity-75">
                    {LEVEL_DESCRIPTION[lv] ?? ""}
                  </div>
                </label>
              ))}
            </fieldset>

            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="text-zinc-700 dark:text-zinc-300">
                  Label (optional)
                </span>
                <input
                  type="text"
                  value={label}
                  maxLength={120}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="PII, EU customers"
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
                />
              </label>
              <label className="block text-sm">
                <span className="text-zinc-700 dark:text-zinc-300">
                  Handling contact (optional)
                </span>
                <input
                  type="text"
                  value={contact}
                  maxLength={200}
                  onChange={(e) => setContact(e.target.value)}
                  placeholder="dpo@example.com"
                  className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm focus:border-zinc-900 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
                />
              </label>
            </div>

            {willRequireAck && (
              <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                <Warning size={16} weight="duotone" className="mt-0.5 shrink-0" />
                <div>
                  Setting this workspace to{" "}
                  <span className="font-medium">restricted</span> will
                  immediately require every call to{" "}
                  <code className="rounded bg-amber-100 px-1 py-0.5 dark:bg-amber-900">
                    /v1/privacy/workspace-export
                  </code>{" "}
                  to include an{" "}
                  <code className="rounded bg-amber-100 px-1 py-0.5 dark:bg-amber-900">
                    X-Classification-Ack: restricted
                  </code>{" "}
                  header.
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
                {saving ? "Saving" : "Save classification"}
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
                Saved. Label is active on the next request.
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
