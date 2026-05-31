"use client";

/**
 * Activity inbox.
 *
 * Single chronological feed of what happened on the account:
 * saved matches and webhook deliveries. We persist the last-seen
 * timestamp in localStorage so badge counts in the nav reflect
 * "what is new since I last looked here".
 */

import Link from "next/link";
import useSWR from "swr";
import { useEffect, useMemo, useState } from "react";
import {
  Bell,
  CheckCircle,
  WarningCircle,
  WaveSine,
  Broadcast,
  ArrowsClockwise,
  EyeSlash,
} from "@phosphor-icons/react/dist/ssr";
import { ACTIVITY_LAST_SEEN_KEY, ACTIVITY_LAST_SEEN_EVENT } from "@/lib/activity";

type Kind = "" | "match" | "delivery";

interface Item {
  id: string;
  kind: "match" | "delivery";
  title: string;
  subtitle: string;
  ok: boolean;
  created_at: number;
  href: string;
}

interface Resp {
  items: Item[];
  total: number;
  latest_at: number;
}

const fetcher = async (url: string): Promise<Resp> => {
  const r = await fetch(url, { cache: "no-store" });
  if (r.status === 401) throw new Error("set an api key in settings to load activity");
  if (!r.ok) throw new Error(`request failed (${r.status})`);
  return r.json();
};

function fmtAgo(ts: number): string {
  if (!ts) return "";
  const ms = Date.now() - ts * 1000;
  if (ms < 0) return "just now";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

export default function ActivityPage() {
  const [kind, setKind] = useState<Kind>("");
  const [q, setQ] = useState("");
  const url = `/api/activity?limit=100${kind ? `&kind=${kind}` : ""}`;
  const { data, error, isLoading, mutate } = useSWR<Resp>(url, fetcher, {
    refreshInterval: 30_000,
  });

  // Mark everything visible as seen.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!data || data.latest_at <= 0) return;
    try {
      const prev = Number(localStorage.getItem(ACTIVITY_LAST_SEEN_KEY) || "0");
      if (data.latest_at > prev) {
        localStorage.setItem(ACTIVITY_LAST_SEEN_KEY, String(data.latest_at));
        window.dispatchEvent(new CustomEvent(ACTIVITY_LAST_SEEN_EVENT));
      }
    } catch {
      /* storage disabled */
    }
  }, [data]);

  const filtered = useMemo<Item[]>(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return data.items;
    return data.items.filter(
      (i) =>
        i.title.toLowerCase().includes(needle) ||
        i.subtitle.toLowerCase().includes(needle),
    );
  }, [data, q]);

  return (
    <div className="px-4 sm:px-6 py-6 max-w-5xl mx-auto">
      <header className="flex flex-wrap items-end justify-between gap-3 mb-4">
        <div>
          <h1 className="font-mono text-sm uppercase tracking-widest flex items-center gap-2 text-[var(--color-text)]">
            <Bell size={16} weight="duotone" className="text-[var(--color-phosphor)]" />
            activity
          </h1>
          <p className="text-xs text-[var(--color-muted)] mt-1 max-w-xl">
            Every match you save and every webhook delivery your account fires, in one timeline.
            New items since your last visit are highlighted.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          className="flex items-center gap-1 px-2 py-1 border border-[var(--color-line)] hover:bg-[var(--color-panel)] font-mono text-[10px] uppercase tracking-widest"
        >
          <ArrowsClockwise size={12} weight="duotone" />
          refresh
        </button>
      </header>

      <div className="flex flex-wrap gap-2 mb-4">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="filter title or detail"
          aria-label="filter activity"
          className="flex-1 min-w-[200px] bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1 font-mono text-xs focus:outline-none focus:border-[var(--color-phosphor)]"
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as Kind)}
          aria-label="filter by kind"
          className="bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1 font-mono text-xs"
        >
          <option value="">all kinds</option>
          <option value="match">matches</option>
          <option value="delivery">webhook deliveries</option>
        </select>
      </div>

      {isLoading && (
        <ul className="space-y-2" aria-busy="true">
          {Array.from({ length: 6 }).map((_, i) => (
            <li
              key={i}
              className="h-14 border border-[var(--color-line)] bg-[var(--color-panel)]/40 animate-pulse"
            />
          ))}
        </ul>
      )}

      {error && !isLoading && (
        <div className="border border-[var(--color-line)] bg-[var(--color-panel)] p-4 font-mono text-xs text-[var(--color-muted)]">
          {String((error as Error).message)}
        </div>
      )}

      {!isLoading && !error && data && filtered.length === 0 && (
        <div className="border border-dashed border-[var(--color-line)] p-8 text-center">
          <EyeSlash size={24} weight="duotone" className="mx-auto text-[var(--color-dim)]" />
          <p className="mt-2 font-mono text-xs text-[var(--color-muted)] uppercase tracking-widest">
            nothing here yet
          </p>
          <p className="mt-1 text-xs text-[var(--color-dim)]">
            Save a match from the capture page or register a webhook to see activity flow in.
          </p>
        </div>
      )}

      {!isLoading && !error && filtered.length > 0 && (
        <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)]">
          {filtered.map((it) => {
            const Icon = it.kind === "match" ? WaveSine : Broadcast;
            const status = it.ok ? CheckCircle : WarningCircle;
            const StatusIcon = it.kind === "delivery" ? status : null;
            return (
              <li key={it.id}>
                <Link
                  href={it.href}
                  className="flex items-start gap-3 px-3 py-3 hover:bg-[var(--color-panel)]/60 focus:bg-[var(--color-panel)] focus:outline-none"
                >
                  <Icon
                    size={18}
                    weight="duotone"
                    className={
                      it.ok
                        ? "text-[var(--color-phosphor)] shrink-0 mt-0.5"
                        : "text-amber-400 shrink-0 mt-0.5"
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-[var(--color-text)] truncate">
                        {it.title}
                      </span>
                      {StatusIcon && (
                        <StatusIcon
                          size={12}
                          weight="duotone"
                          className={it.ok ? "text-[var(--color-phosphor)]" : "text-amber-400"}
                        />
                      )}
                    </div>
                    <p className="text-[11px] text-[var(--color-muted)] truncate">{it.subtitle}</p>
                  </div>
                  <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest shrink-0 mt-0.5">
                    {fmtAgo(it.created_at)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {data && (
        <p className="mt-3 text-[10px] font-mono text-[var(--color-dim)] uppercase tracking-widest">
          showing {filtered.length} of {data.total}
        </p>
      )}
    </div>
  );
}
