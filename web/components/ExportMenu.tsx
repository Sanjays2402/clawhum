"use client";

import { useEffect, useRef, useState } from "react";
import { DownloadSimple, FileCsv, Code, CaretDown } from "@phosphor-icons/react";
import type { StoredMatch } from "@/lib/history";
import {
  buildJsonExport,
  downloadBlob,
  flattenMatches,
  rowsToCsv,
  timestampSlug,
} from "@/lib/export";

interface Props {
  matches: StoredMatch[];
  /** Override file stem; default `clawhum-matches-<timestamp>`. */
  stem?: string;
  /** Render variant: full dropdown menu or compact inline buttons. */
  variant?: "menu" | "inline";
  /** Disabled when the source set is empty. */
  disabled?: boolean;
}

export default function ExportMenu({ matches, stem, variant = "menu", disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"csv" | "json" | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const isDisabled = disabled || matches.length === 0;
  const base = stem || `clawhum-matches-${timestampSlug()}`;

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function doExport(kind: "csv" | "json") {
    if (isDisabled) return;
    setBusy(kind);
    try {
      if (kind === "csv") {
        const csv = rowsToCsv(flattenMatches(matches));
        downloadBlob(`${base}.csv`, "text/csv;charset=utf-8", csv);
      } else {
        const json = JSON.stringify(buildJsonExport(matches), null, 2);
        downloadBlob(`${base}.json`, "application/json", json);
      }
    } finally {
      setBusy(null);
      setOpen(false);
    }
  }

  if (variant === "inline") {
    return (
      <div className="flex items-center gap-2" data-testid="export-inline">
        <button
          type="button"
          onClick={() => doExport("csv")}
          disabled={isDisabled || busy !== null}
          aria-label="download csv"
          className="btn-ghost px-3 py-1.5 rounded-[2px] font-mono text-[11px] uppercase tracking-widest flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <FileCsv size={13} weight="duotone" />
          {busy === "csv" ? "exporting..." : "csv"}
        </button>
        <button
          type="button"
          onClick={() => doExport("json")}
          disabled={isDisabled || busy !== null}
          aria-label="download json"
          className="btn-ghost px-3 py-1.5 rounded-[2px] font-mono text-[11px] uppercase tracking-widest flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Code size={13} weight="duotone" />
          {busy === "json" ? "exporting..." : "json"}
        </button>
      </div>
    );
  }

  return (
    <div ref={rootRef} className="relative" data-testid="export-menu">
      <button
        type="button"
        onClick={() => !isDisabled && setOpen(o => !o)}
        disabled={isDisabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="export query log"
        className="btn-ghost px-3 py-1.5 rounded-[2px] font-mono text-[11px] uppercase tracking-widest flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <DownloadSimple size={13} weight="duotone" />
        export
        <span className="text-[var(--color-dim)] tabular-nums">{matches.length}</span>
        <CaretDown size={11} weight="duotone" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-1 min-w-[200px] z-20 panel rounded-[2px] py-1 shadow-lg"
        >
          <button
            role="menuitem"
            type="button"
            onClick={() => doExport("csv")}
            disabled={busy !== null}
            className="w-full text-left px-3 py-2 font-mono text-[11px] uppercase tracking-widest flex items-center gap-2 hover:bg-[var(--color-line)] disabled:opacity-50"
          >
            <FileCsv size={13} weight="duotone" className="text-[var(--color-phosphor)]" />
            <span>csv</span>
            <span className="ml-auto text-[10px] text-[var(--color-dim)] normal-case tracking-normal">flat / one row per candidate</span>
          </button>
          <button
            role="menuitem"
            type="button"
            onClick={() => doExport("json")}
            disabled={busy !== null}
            className="w-full text-left px-3 py-2 font-mono text-[11px] uppercase tracking-widest flex items-center gap-2 hover:bg-[var(--color-line)] disabled:opacity-50"
          >
            <Code size={13} weight="duotone" className="text-[var(--color-phosphor)]" />
            <span>json</span>
            <span className="ml-auto text-[10px] text-[var(--color-dim)] normal-case tracking-normal">nested / one object per query</span>
          </button>
        </div>
      )}
    </div>
  );
}
