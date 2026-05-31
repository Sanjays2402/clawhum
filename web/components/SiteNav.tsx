"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS: { href: string; label: string; hint: string }[] = [
  { href: "/", label: "capture", hint: "record + match" },
  { href: "/demo", label: "demo", hint: "try a sample" },
  { href: "/matches", label: "matches", hint: "query log" },
  { href: "/batch", label: "batch", hint: "zip in, csv out" },
  { href: "/history", label: "history", hint: "cloud, synced" },
  { href: "/insights", label: "insights", hint: "local analytics" },
  { href: "/catalog", label: "catalog", hint: "fingerprinted tracks" },
  { href: "/metrics", label: "metrics", hint: "prometheus" },
  { href: "/library", label: "index", hint: "reindex / stats" },
  { href: "/usage", label: "usage", hint: "quota + meter" },
  { href: "/settings", label: "settings", hint: "api key + usage" },
  { href: "/webhooks", label: "webhooks", hint: "outbound events" },
];

export default function SiteNav() {
  const path = usePathname();
  return (
    <nav className="border-b border-[var(--color-line)] bg-[var(--color-bg)] flex items-stretch h-10">
      <Link href="/" className="flex items-center gap-2 px-4 border-r border-[var(--color-line)] hover:bg-[var(--color-panel)]">
        <span className="led-dot" />
        <span className="font-mono text-[12px] tracking-widest">
          <span className="text-[var(--color-phosphor)]">CLAW</span><span className="text-[var(--color-text)]">HUM</span>
        </span>
        <span className="font-mono text-[9px] text-[var(--color-dim)] uppercase tracking-widest ml-1">v0.2</span>
      </Link>
      <div className="flex">
        {TABS.map(t => {
          const active = t.href === "/" ? path === "/" : path.startsWith(t.href);
          return (
            <Link
              key={t.href}
              href={t.href}
              className={`group flex flex-col justify-center px-4 border-r border-[var(--color-line)] transition
                ${active
                  ? "bg-[var(--color-panel)] text-[var(--color-phosphor)]"
                  : "text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-panel)]"
                }`}
            >
              <span className="font-mono text-[11px] uppercase tracking-widest leading-none">{t.label}</span>
              <span className="font-mono text-[9px] text-[var(--color-dim)] mt-0.5 leading-none uppercase tracking-widest">{t.hint}</span>
            </Link>
          );
        })}
      </div>
      <div className="ml-auto flex items-center px-4 font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
        api / 127.0.0.1:7451
      </div>
    </nav>
  );
}
