"use client";

import { useEffect, useMemo, useState } from "react";
import { Code, Check, Copy } from "@phosphor-icons/react/dist/ssr";

interface Props {
  shareId: string;
}

/**
 * Renders the iframe embed snippet for a public share. We resolve the
 * origin from the live window so the snippet pasted by the viewer always
 * matches the host they actually loaded the page from (preview, prod,
 * tunnel, whatever). Server-rendered origin would lie in those cases.
 */
export default function EmbedSnippet({ shareId }: Props) {
  const [origin, setOrigin] = useState<string>("");
  const [width, setWidth] = useState<number>(480);
  const [height, setHeight] = useState<number>(360);
  const [copied, setCopied] = useState<"" | "html" | "url">("");

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  const html = useMemo(() => {
    if (!origin) return "";
    const src = `${origin}/r/${encodeURIComponent(shareId)}/embed`;
    return (
      `<iframe src="${src}" width="${width}" height="${height}" ` +
      `frameborder="0" loading="lazy" allow="clipboard-write" ` +
      `referrerpolicy="no-referrer-when-downgrade" ` +
      `title="clawhum shared match ${shareId}" ` +
      `style="border:0;border-radius:2px;max-width:100%;"></iframe>`
    );
  }, [origin, shareId, width, height]);

  const embedUrl = origin ? `${origin}/r/${encodeURIComponent(shareId)}/embed` : "";

  async function copy(what: "html" | "url") {
    const text = what === "html" ? html : embedUrl;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback: select the textarea so the user can copy manually.
      const ta = document.getElementById("clawhum-embed-html") as HTMLTextAreaElement | null;
      if (ta) {
        ta.focus();
        ta.select();
      }
      return;
    }
    setCopied(what);
    window.setTimeout(() => setCopied(""), 1500);
  }

  return (
    <section className="panel rounded-[2px]">
      <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between gap-2 flex-wrap">
        <span className="label-xs flex items-center gap-1.5">
          <Code size={12} weight="duotone" />
          embed this match
        </span>
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <label htmlFor="embed-w" className="sr-only">width</label>
          <span>w</span>
          <input
            id="embed-w"
            type="number"
            min={240}
            max={960}
            step={10}
            value={width}
            onChange={(e) => setWidth(clamp(Number(e.target.value), 240, 960, 480))}
            className="w-16 bg-transparent border border-[var(--color-line)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
          />
          <span aria-hidden="true">x</span>
          <label htmlFor="embed-h" className="sr-only">height</label>
          <span>h</span>
          <input
            id="embed-h"
            type="number"
            min={180}
            max={720}
            step={10}
            value={height}
            onChange={(e) => setHeight(clamp(Number(e.target.value), 180, 720, 360))}
            className="w-16 bg-transparent border border-[var(--color-line)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
          />
        </div>
      </div>

      <div className="px-3 py-3 space-y-3">
        <label htmlFor="clawhum-embed-html" className="sr-only">embed html</label>
        <textarea
          id="clawhum-embed-html"
          readOnly
          value={html || "loading..."}
          rows={3}
          className="w-full bg-[color:var(--color-surface,#0e1011)] border border-[var(--color-line)] px-2 py-1.5 font-mono text-[11px] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)] resize-y"
          onFocus={(e) => e.currentTarget.select()}
          aria-label="iframe embed code"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => copy("html")}
            disabled={!html}
            className="flex items-center gap-1.5 px-2 py-1 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-text)] disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Copy embed HTML"
          >
            {copied === "html" ? (
              <>
                <Check size={11} weight="duotone" className="text-[var(--color-phosphor)]" />
                copied
              </>
            ) : (
              <>
                <Copy size={11} weight="duotone" />
                copy html
              </>
            )}
          </button>
          <button
            type="button"
            onClick={() => copy("url")}
            disabled={!embedUrl}
            className="flex items-center gap-1.5 px-2 py-1 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-text)] disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Copy embed URL"
          >
            {copied === "url" ? (
              <>
                <Check size={11} weight="duotone" className="text-[var(--color-phosphor)]" />
                copied
              </>
            ) : (
              <>
                <Copy size={11} weight="duotone" />
                copy url
              </>
            )}
          </button>
          <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
            also discoverable via <code className="text-[var(--color-phosphor)]">/api/oembed</code>
          </span>
        </div>
      </div>
    </section>
  );
}

function clamp(n: number, lo: number, hi: number, fallback: number): number {
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(hi, Math.max(lo, Math.floor(n)));
}
