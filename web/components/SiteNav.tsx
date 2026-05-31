"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ACTIVITY_LAST_SEEN_EVENT, getLastSeen } from "@/lib/activity";

const TABS: { href: string; label: string; hint: string }[] = [
  { href: "/", label: "capture", hint: "record + match" },
  { href: "/demo", label: "demo", hint: "try a sample" },
  { href: "/activity", label: "activity", hint: "inbox" },
  { href: "/matches", label: "matches", hint: "query log" },
  { href: "/batch", label: "batch", hint: "zip in, csv out" },
  { href: "/history", label: "history", hint: "cloud, synced" },
  { href: "/insights", label: "insights", hint: "local analytics" },
  { href: "/catalog", label: "catalog", hint: "fingerprinted tracks" },
  { href: "/metrics", label: "metrics", hint: "prometheus" },
  { href: "/library", label: "index", hint: "reindex / stats" },
  { href: "/usage", label: "usage", hint: "quota + meter" },
  { href: "/pricing", label: "pricing", hint: "plans + faq" },
  { href: "/settings", label: "settings", hint: "api key + usage" },
  { href: "/webhooks", label: "webhooks", hint: "outbound events" },
  { href: "/shares", label: "shares", hint: "public links" },
];

// Polls /api/activity for the latest timestamp and compares to the
// stored lastSeen cursor so the nav can light an unread dot.
function useUnreadActivity(): boolean {
  const [unread, setUnread] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        const r = await fetch("/api/activity?limit=1", { cache: "no-store" });
        if (!r.ok) return; // 401 etc. just means no key yet
        const j = (await r.json()) as { latest_at?: number };
        const latest = Number(j.latest_at || 0);
        if (cancelled) return;
        setUnread(latest > 0 && latest > getLastSeen());
      } catch {
        /* ignore network blips */
      }
    }

    check();
    const id = window.setInterval(check, 45_000);
    const onSeen = () => setUnread(false);
    window.addEventListener(ACTIVITY_LAST_SEEN_EVENT, onSeen);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      window.removeEventListener(ACTIVITY_LAST_SEEN_EVENT, onSeen);
    };
  }, []);

  return unread;
}

export default function SiteNav() {
  const path = usePathname();
  const unread = useUnreadActivity();
  return (
    <nav className="border-b border-[var(--color-line)] bg-[var(--color-bg)] flex items-stretch h-10 overflow-x-auto">
      <Link href="/" className="flex items-center gap-2 px-4 border-r border-[var(--color-line)] hover:bg-[var(--color-panel)] shrink-0">
        <span className="led-dot" />
        <span className="font-mono text-[12px] tracking-widest">
          <span className="text-[var(--color-phosphor)]">CLAW</span><span className="text-[var(--color-text)]">HUM</span>
        </span>
        <span className="font-mono text-[9px] text-[var(--color-dim)] uppercase tracking-widest ml-1">v0.2</span>
      </Link>
      <div className="flex">
        {TABS.map(t => {
          const active = t.href === "/" ? path === "/" : path.startsWith(t.href);
          const showDot = t.href === "/activity" && unread && !active;
          return (
            <Link
              key={t.href}
              href={t.href}
              aria-label={showDot ? `${t.label}, unread items` : undefined}
              className={`group flex flex-col justify-center px-4 border-r border-[var(--color-line)] transition relative shrink-0
                ${active
                  ? "bg-[var(--color-panel)] text-[var(--color-phosphor)]"
                  : "text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)]"
                }`}
            >
              <span className="font-mono text-[11px] uppercase tracking-widest leading-none">{t.label}</span>
              <span className="font-mono text-[9px] text-[var(--color-dim)] mt-0.5 leading-none uppercase tracking-widest">{t.hint}</span>
              {showDot && (
                <span
                  aria-hidden
                  className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-[var(--color-phosphor)] shadow-[0_0_6px_var(--color-phosphor)]"
                />
              )}
            </Link>
          );
        })}
      </div>
      <div className="ml-auto flex items-center px-4 font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest shrink-0">
        api / 127.0.0.1:7451
      </div>
    </nav>
  );
}
