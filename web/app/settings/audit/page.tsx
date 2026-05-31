"use client";

/**
 * Workspace audit log search and export.
 *
 * Admins can scan every mutating request that touched their workspace,
 * filter by actor, method, path prefix, status, and time window, and
 * download the matching rows as CSV or JSON for compliance review.
 *
 * Reads are tenant scoped server side; this page never sees rows from
 * other workspaces. Non admin keys get 403 from the API and the page
 * renders a clear "admin only" message instead of an empty table.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowsClockwise,
  DownloadSimple,
  MagnifyingGlass,
  ShieldCheck,
  Warning,
} from "@phosphor-icons/react/dist/ssr";
import { getApiKey, useApiKey } from "@/lib/apiKey";

interface AuditEvent {
  ts: number;
  actor: string;
  api_key_name: string | null;
  tenant_id: string | null;
  roles: string[];
  method: string;
  path: string;
  status: number;
  request_id: string | null;
  trace_id: string | null;
  client_ip: string | null;
  user_agent: string | null;
  duration_ms: number | null;
  dry_run: boolean;
}

interface ListResp {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  truncated: boolean;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: ListResp }
  | { kind: "error"; status: number; message: string };

const PAGE_SIZE = 50;
const METHODS = ["", "POST", "PUT", "PATCH", "DELETE"] as const;
const DRY_RUN_OPTIONS = [
  { value: "any", label: "any" },
  { value: "only", label: "dry-run only" },
  { value: "exclude", label: "exclude dry-run" },
] as const;

function authHeaders(): Record<string, string> {
  const k = getApiKey();
  return k ? { "X-API-Key": k } : {};
}

function statusTone(s: number): string {
  if (s >= 500) return "text-red-400";
  if (s >= 400) return "text-amber-400";
  if (s >= 300) return "text-sky-400";
  if (s >= 200) return "text-emerald-400";
  return "text-[var(--color-muted)]";
}

function methodTone(m: string): string {
  switch (m) {
    case "POST":
      return "border-emerald-700/60 text-emerald-300";
    case "PUT":
    case "PATCH":
      return "border-sky-700/60 text-sky-300";
    case "DELETE":
      return "border-red-700/60 text-red-300";
    default:
      return "border-[var(--color-line)] text-[var(--color-muted)]";
  }
}

function formatTs(ts: number): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

export default function AuditLogPage() {
  const [storedKey] = useApiKey();
  const [state, setState] = useState<State>({ kind: "idle" });
  const [q, setQ] = useState("");
  const [actor, setActor] = useState("");
  const [method, setMethod] = useState<string>("");
  const [pathPrefix, setPathPrefix] = useState("");
  const [statusMin, setStatusMin] = useState("");
  const [statusMax, setStatusMax] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [dryRun, setDryRun] = useState<string>("any");
  const [offset, setOffset] = useState(0);

  const buildQuery = useCallback(
    (extra?: Record<string, string | number>) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (actor) params.set("actor", actor);
      if (method) params.set("method", method);
      if (pathPrefix) params.set("path", pathPrefix);
      if (statusMin) params.set("status_min", statusMin);
      if (statusMax) params.set("status_max", statusMax);
      if (since) {
        const s = Math.floor(new Date(since).getTime() / 1000);
        if (s > 0) params.set("since", String(s));
      }
      if (until) {
        const u = Math.floor(new Date(until).getTime() / 1000);
        if (u > 0) params.set("until", String(u));
      }
      if (dryRun && dryRun !== "any") params.set("dry_run", dryRun);
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(offset));
      if (extra) {
        for (const [k, v] of Object.entries(extra)) {
          params.set(k, String(v));
        }
      }
      return params.toString();
    },
    [q, actor, method, pathPrefix, statusMin, statusMax, since, until, dryRun, offset],
  );

  const load = useCallback(async () => {
    if (!storedKey) {
      setState({ kind: "error", status: 401, message: "missing api key. set one on /settings first." });
      return;
    }
    setState({ kind: "loading" });
    try {
      const r = await fetch(`/api/audit?${buildQuery()}`, { headers: authHeaders() });
      if (r.status === 403) {
        setState({
          kind: "error",
          status: 403,
          message: "admin role required to view the audit log. ask your workspace owner.",
        });
        return;
      }
      if (!r.ok) {
        const body = await r.text();
        setState({ kind: "error", status: r.status, message: body || r.statusText });
        return;
      }
      const data = (await r.json()) as ListResp;
      setState({ kind: "ready", data });
    } catch (err) {
      setState({
        kind: "error",
        status: 0,
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }, [storedKey, buildQuery]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setOffset(0);
      void load();
    },
    [load],
  );

  const exportAs = useCallback(
    (format: "csv" | "json") => {
      // Build URL without offset/limit so the download contains everything
      // matching the active filters, up to the server side cap.
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (actor) params.set("actor", actor);
      if (method) params.set("method", method);
      if (pathPrefix) params.set("path", pathPrefix);
      if (statusMin) params.set("status_min", statusMin);
      if (statusMax) params.set("status_max", statusMax);
      if (since) {
        const s = Math.floor(new Date(since).getTime() / 1000);
        if (s > 0) params.set("since", String(s));
      }
      if (until) {
        const u = Math.floor(new Date(until).getTime() / 1000);
        if (u > 0) params.set("until", String(u));
      }
      if (dryRun && dryRun !== "any") params.set("dry_run", dryRun);
      params.set("format", format);
      // Fetch with auth header, then trigger a download from the blob so
      // we never need to embed the api key in a query string.
      void (async () => {
        try {
          const r = await fetch(`/api/audit/export?${params.toString()}`, { headers: authHeaders() });
          if (!r.ok) {
            const body = await r.text();
            alert(`export failed: ${r.status} ${body || r.statusText}`);
            return;
          }
          const blob = await r.blob();
          const dispo = r.headers.get("content-disposition") || "";
          const match = dispo.match(/filename="([^"]+)"/);
          const fname = match ? match[1] : `clawhum-audit.${format}`;
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = fname;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        } catch (err) {
          alert(`export failed: ${err instanceof Error ? err.message : String(err)}`);
        }
      })();
    },
    [q, actor, method, pathPrefix, statusMin, statusMax, since, until, dryRun],
  );

  const ready = state.kind === "ready" ? state.data : null;
  const total = ready?.total ?? 0;
  const showingFrom = ready && ready.items.length > 0 ? offset + 1 : 0;
  const showingTo = ready ? offset + ready.items.length : 0;

  return (
    <div className="min-h-dvh bg-[var(--color-bg)] text-[var(--color-fg)]">
      <div className="mx-auto max-w-6xl px-4 py-8 space-y-6">
        <header className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3">
            <Link
              href="/settings"
              className="inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              <ArrowLeft weight="duotone" size={14} />
              settings
            </Link>
            <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-phosphor)] flex items-center gap-2">
              <ShieldCheck weight="duotone" size={18} />
              audit log
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void load()}
              className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              <ArrowsClockwise weight="duotone" size={12} />
              refresh
            </button>
            <button
              type="button"
              onClick={() => exportAs("csv")}
              disabled={state.kind !== "ready"}
              className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-40"
            >
              <DownloadSimple weight="duotone" size={12} />
              csv
            </button>
            <button
              type="button"
              onClick={() => exportAs("json")}
              disabled={state.kind !== "ready"}
              className="inline-flex items-center gap-1 border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] disabled:opacity-40"
            >
              <DownloadSimple weight="duotone" size={12} />
              json
            </button>
          </div>
        </header>

        <p className="font-mono text-[10px] leading-relaxed text-[var(--color-dim)]">
          every mutating request to your workspace is captured here with actor, method, path, status,
          ip, and request id. rows are scoped to your tenant on the server; you can never see another
          workspace. exports respect the active filters and are capped per request to protect memory.
        </p>

        <form
          onSubmit={onSubmit}
          className="panel rounded-[2px] p-4 grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-3"
        >
          <label className="flex flex-col gap-1">
            <span className="label-xs">search</span>
            <div className="flex items-center gap-1 border border-[var(--color-line)] px-2">
              <MagnifyingGlass weight="duotone" size={12} className="text-[var(--color-dim)]" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="actor, path, request id, user agent"
                className="w-full bg-transparent py-1.5 font-mono text-[12px] outline-none placeholder:text-[var(--color-dim)]"
              />
            </div>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">actor</span>
            <input
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              placeholder="key:..."
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none placeholder:text-[var(--color-dim)]"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">method</span>
            <select
              value={method}
              onChange={(e) => setMethod(e.target.value)}
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none"
            >
              {METHODS.map((m) => (
                <option key={m} value={m}>
                  {m || "any"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">path starts with</span>
            <input
              value={pathPrefix}
              onChange={(e) => setPathPrefix(e.target.value)}
              placeholder="/keys"
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none placeholder:text-[var(--color-dim)]"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">status min</span>
            <input
              type="number"
              min={0}
              max={599}
              value={statusMin}
              onChange={(e) => setStatusMin(e.target.value)}
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">status max</span>
            <input
              type="number"
              min={0}
              max={599}
              value={statusMax}
              onChange={(e) => setStatusMax(e.target.value)}
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">since</span>
            <input
              type="datetime-local"
              value={since}
              onChange={(e) => setSince(e.target.value)}
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">until</span>
            <input
              type="datetime-local"
              value={until}
              onChange={(e) => setUntil(e.target.value)}
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-xs">dry run</span>
            <select
              value={dryRun}
              onChange={(e) => setDryRun(e.target.value)}
              className="border border-[var(--color-line)] bg-transparent px-2 py-1.5 font-mono text-[12px] outline-none"
            >
              {DRY_RUN_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end gap-2 md:col-span-2">
            <button
              type="submit"
              className="border border-[var(--color-phosphor)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)] hover:bg-[var(--color-phosphor)] hover:text-[var(--color-bg)]"
            >
              search
            </button>
            <button
              type="button"
              onClick={() => {
                setQ("");
                setActor("");
                setMethod("");
                setPathPrefix("");
                setStatusMin("");
                setStatusMax("");
                setSince("");
                setUntil("");
                setDryRun("any");
                setOffset(0);
              }}
              className="border border-[var(--color-line)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)]"
            >
              clear
            </button>
          </div>
        </form>

        {state.kind === "loading" ? (
          <div className="panel rounded-[2px] p-4 space-y-2" aria-busy="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-6 w-full animate-pulse bg-[var(--color-line)]/40"
              />
            ))}
          </div>
        ) : null}

        {state.kind === "error" ? (
          <div className="panel rounded-[2px] p-4 flex items-start gap-2 text-amber-400">
            <Warning weight="duotone" size={16} className="mt-0.5 shrink-0" />
            <div className="font-mono text-[11px] leading-relaxed">
              <div className="uppercase tracking-widest">error {state.status || ""}</div>
              <div className="text-[var(--color-muted)] mt-1 break-all">{state.message}</div>
            </div>
          </div>
        ) : null}

        {ready ? (
          ready.items.length === 0 ? (
            <div className="panel rounded-[2px] p-6 text-center font-mono text-[11px] text-[var(--color-dim)]">
              no events match these filters yet.
            </div>
          ) : (
            <div className="panel rounded-[2px] overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left font-mono text-[11px]">
                  <thead className="text-[var(--color-dim)] uppercase tracking-widest text-[10px]">
                    <tr className="border-b border-[var(--color-line)]">
                      <th className="px-3 py-2">when</th>
                      <th className="px-3 py-2">actor</th>
                      <th className="px-3 py-2">method</th>
                      <th className="px-3 py-2">path</th>
                      <th className="px-3 py-2">status</th>
                      <th className="px-3 py-2 hidden md:table-cell">ip</th>
                      <th className="px-3 py-2 hidden lg:table-cell">request id</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ready.items.map((e, idx) => (
                      <tr
                        key={`${e.ts}-${e.request_id || idx}`}
                        className="border-b border-[var(--color-line)]/60 hover:bg-[var(--color-line)]/20"
                      >
                        <td className="px-3 py-2 whitespace-nowrap text-[var(--color-muted)]">
                          {formatTs(e.ts)}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <span className="text-[var(--color-fg)]">{e.actor}</span>
                          {e.api_key_name ? (
                            <span className="ml-1 text-[var(--color-dim)]">
                              ({e.api_key_name})
                            </span>
                          ) : null}
                          {e.dry_run ? (
                            <span className="ml-2 border border-amber-700/60 px-1 text-[9px] uppercase tracking-widest text-amber-400">
                              dry
                            </span>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <span
                            className={`border px-1.5 py-0.5 text-[10px] uppercase tracking-widest ${methodTone(e.method)}`}
                          >
                            {e.method}
                          </span>
                        </td>
                        <td className="px-3 py-2 break-all">{e.path}</td>
                        <td className={`px-3 py-2 whitespace-nowrap ${statusTone(e.status)}`}>
                          {e.status}
                        </td>
                        <td className="px-3 py-2 hidden md:table-cell whitespace-nowrap text-[var(--color-muted)]">
                          {e.client_ip || ""}
                        </td>
                        <td className="px-3 py-2 hidden lg:table-cell whitespace-nowrap text-[var(--color-dim)]">
                          {e.request_id ? e.request_id.slice(0, 12) : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-between gap-3 border-t border-[var(--color-line)] px-3 py-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
                <span>
                  showing {showingFrom}-{showingTo} of {total}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      const next = Math.max(0, offset - PAGE_SIZE);
                      setOffset(next);
                    }}
                    disabled={offset === 0}
                    className="border border-[var(--color-line)] px-2 py-1 hover:text-[var(--color-phosphor)] disabled:opacity-40"
                  >
                    prev
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const next = offset + PAGE_SIZE;
                      if (next < total) setOffset(next);
                    }}
                    disabled={offset + PAGE_SIZE >= total}
                    className="border border-[var(--color-line)] px-2 py-1 hover:text-[var(--color-phosphor)] disabled:opacity-40"
                  >
                    next
                  </button>
                </div>
              </div>
            </div>
          )
        ) : null}
      </div>
    </div>
  );
}
