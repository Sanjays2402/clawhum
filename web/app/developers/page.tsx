"use client";

/**
 * Developers page.
 *
 * One place where an integrator can see every public, version-pinned
 * endpoint, copy a working curl / python / JS snippet pre-filled with
 * their own API key, and learn the URL conventions we promise to keep
 * stable. Everything under /v1/* on the backend (proxied as /api/v1/*
 * from the web) is documented here. The unversioned /api/* routes that
 * power the in-app UI are intentionally not advertised here so that
 * customer integrations target /v1.
 *
 * The page renders entirely client side so the key never leaves the
 * browser, matching the rest of the app's auth model.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Code,
  Copy,
  Check,
  Key,
  ShieldCheck,
  Terminal,
  ArrowSquareOut,
} from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import { maskKey, useApiKey } from "@/lib/apiKey";

type Lang = "curl" | "python" | "javascript";

interface Endpoint {
  id: string;
  method: "GET" | "POST" | "DELETE" | "PATCH";
  path: string; // e.g. /v1/match
  summary: string;
  body?: string; // short body description shown in the row
  snippet: (origin: string, key: string) => Record<Lang, string>;
}

function ph(key: string): string {
  return key || "YOUR_API_KEY";
}

const ENDPOINTS: Endpoint[] = [
  {
    id: "match",
    method: "POST",
    path: "/v1/match",
    summary: "match a hum or melody clip against the indexed catalog",
    body: "multipart/form-data: audio (file), top_k (int), threshold (float)",
    snippet: (origin, key) => ({
      curl: `curl -X POST ${origin}/api/v1/match \\
  -H "X-API-Key: ${ph(key)}" \\
  -F "audio=@hum.wav" \\
  -F "top_k=5" \\
  -F "threshold=0.2"`,
      python: `import requests

with open("hum.wav", "rb") as f:
    r = requests.post(
        "${origin}/api/v1/match",
        headers={"X-API-Key": "${ph(key)}"},
        files={"audio": f},
        data={"top_k": 5, "threshold": 0.2},
        timeout=30,
    )
r.raise_for_status()
print(r.json())`,
      javascript: `const fd = new FormData();
fd.append("audio", file); // a Blob or File
fd.append("top_k", "5");
fd.append("threshold", "0.2");

const r = await fetch("${origin}/api/v1/match", {
  method: "POST",
  headers: { "X-API-Key": "${ph(key)}" },
  body: fd,
});
if (!r.ok) throw new Error(\`\${r.status} \${await r.text()}\`);
console.log(await r.json());`,
    }),
  },
  {
    id: "batch",
    method: "POST",
    path: "/v1/batch",
    summary: "match every clip in a zip archive in one request",
    body: "multipart/form-data: archive (.zip), format=json|csv",
    snippet: (origin, key) => ({
      curl: `curl -X POST ${origin}/api/v1/batch \\
  -H "X-API-Key: ${ph(key)}" \\
  -F "archive=@clips.zip" \\
  -F "format=csv" \\
  -o results.csv`,
      python: `import requests

with open("clips.zip", "rb") as f:
    r = requests.post(
        "${origin}/api/v1/batch",
        headers={"X-API-Key": "${ph(key)}"},
        files={"archive": f},
        data={"format": "json"},
        timeout=600,
    )
r.raise_for_status()
print(r.json())`,
      javascript: `const fd = new FormData();
fd.append("archive", zipFile);
fd.append("format", "json");

const r = await fetch("${origin}/api/v1/batch", {
  method: "POST",
  headers: { "X-API-Key": "${ph(key)}" },
  body: fd,
});
console.log(await r.json());`,
    }),
  },
  {
    id: "history",
    method: "GET",
    path: "/v1/history",
    summary: "list saved matches for your tenant, with search and paging",
    body: "query params: q, tag, limit, offset, sort, starred",
    snippet: (origin, key) => ({
      curl: `curl "${origin}/api/v1/history?limit=20&offset=0" \\
  -H "X-API-Key: ${ph(key)}"`,
      python: `import requests

r = requests.get(
    "${origin}/api/v1/history",
    headers={"X-API-Key": "${ph(key)}"},
    params={"limit": 20, "offset": 0},
    timeout=30,
)
r.raise_for_status()
print(r.json())`,
      javascript: `const r = await fetch("${origin}/api/v1/history?limit=20&offset=0", {
  headers: { "X-API-Key": "${ph(key)}" },
});
console.log(await r.json());`,
    }),
  },
  {
    id: "history-views",
    method: "POST",
    path: "/v1/history/views",
    summary: "save a named filter combination (search query, tag, sort, starred) for the history page",
    body: "json: { name, filters: { q, tag, sort, starred } }",
    snippet: (origin, key) => ({
      curl: `curl -X POST "${origin}/api/v1/history/views" \\
  -H "X-API-Key: ${ph(key)}" \\
  -H "Content-Type: application/json" \\
  -d '{"name":"Top jazz","filters":{"q":"","tag":"jazz","sort":"top_score","starred":true}}'`,
      python: `import requests

r = requests.post(
    "${origin}/api/v1/history/views",
    headers={"X-API-Key": "${ph(key)}"},
    json={
        "name": "Top jazz",
        "filters": {"q": "", "tag": "jazz", "sort": "top_score", "starred": True},
    },
    timeout=30,
)
r.raise_for_status()
print(r.json())`,
      javascript: `const r = await fetch("${origin}/api/v1/history/views", {
  method: "POST",
  headers: { "X-API-Key": "${ph(key)}", "Content-Type": "application/json" },
  body: JSON.stringify({
    name: "Top jazz",
    filters: { q: "", tag: "jazz", sort: "top_score", starred: true },
  }),
});
console.log(await r.json());`,
    }),
  },
  {
    id: "history-export",
    method: "GET",
    path: "/v1/history/export",
    summary: "download your full match history as CSV or JSON",
    body: "query params: format=csv|json",
    snippet: (origin, key) => ({
      curl: `curl "${origin}/api/v1/history/export?format=csv" \\
  -H "X-API-Key: ${ph(key)}" \\
  -o history.csv`,
      python: `import requests

r = requests.get(
    "${origin}/api/v1/history/export",
    headers={"X-API-Key": "${ph(key)}"},
    params={"format": "csv"},
    timeout=60,
)
open("history.csv", "wb").write(r.content)`,
      javascript: `const r = await fetch("${origin}/api/v1/history/export?format=csv", {
  headers: { "X-API-Key": "${ph(key)}" },
});
const blob = await r.blob();
// save blob to disk however you like`,
    }),
  },
  {
    id: "share-create",
    method: "POST",
    path: "/v1/share",
    summary: "create a public, read-only share link for a match result",
    body: "application/json: { query_id, elapsed_ms, count, results, ... }",
    snippet: (origin, key) => ({
      curl: `curl -X POST ${origin}/api/v1/share \\
  -H "X-API-Key: ${ph(key)}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "query_id": "abc-123",
    "elapsed_ms": 412,
    "count": 1,
    "results": [{"track_id":"t1","title":"...","score":0.91}],
    "filename": "hum.wav",
    "duration_sec": 6.2
  }'`,
      python: `import requests

payload = {
    "query_id": "abc-123",
    "elapsed_ms": 412,
    "count": 1,
    "results": [{"track_id": "t1", "title": "...", "score": 0.91}],
    "filename": "hum.wav",
    "duration_sec": 6.2,
}
r = requests.post(
    "${origin}/api/v1/share",
    headers={"X-API-Key": "${ph(key)}"},
    json=payload,
    timeout=30,
)
r.raise_for_status()
print(r.json())  # {"id": "...", "url": "${origin}/r/..."}`,
      javascript: `const r = await fetch("${origin}/api/v1/share", {
  method: "POST",
  headers: {
    "X-API-Key": "${ph(key)}",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ query_id: "abc-123", elapsed_ms: 412, count: 1, results: [] }),
});
console.log(await r.json());`,
    }),
  },
  {
    id: "webhooks",
    method: "POST",
    path: "/v1/webhooks",
    summary: "register an outbound webhook that fires on match.completed",
    body: "application/json: { url, events: [\"match.completed\"] }",
    snippet: (origin, key) => ({
      curl: `curl -X POST ${origin}/api/v1/webhooks \\
  -H "X-API-Key: ${ph(key)}" \\
  -H "Content-Type: application/json" \\
  -d '{"url":"https://example.com/hook","events":["match.completed"]}'`,
      python: `import requests

r = requests.post(
    "${origin}/api/v1/webhooks",
    headers={"X-API-Key": "${ph(key)}"},
    json={"url": "https://example.com/hook", "events": ["match.completed"]},
    timeout=30,
)
print(r.json())  # contains the signing secret, shown once`,
      javascript: `const r = await fetch("${origin}/api/v1/webhooks", {
  method: "POST",
  headers: { "X-API-Key": "${ph(key)}", "Content-Type": "application/json" },
  body: JSON.stringify({ url: "https://example.com/hook", events: ["match.completed"] }),
});
console.log(await r.json());`,
    }),
  },
  {
    id: "usage",
    method: "GET",
    path: "/v1/usage",
    summary: "current quota meter: minute, day, month windows",
    snippet: (origin, key) => ({
      curl: `curl ${origin}/api/v1/usage \\
  -H "X-API-Key: ${ph(key)}"`,
      python: `import requests
r = requests.get("${origin}/api/v1/usage", headers={"X-API-Key": "${ph(key)}"})
print(r.json())`,
      javascript: `const r = await fetch("${origin}/api/v1/usage", {
  headers: { "X-API-Key": "${ph(key)}" },
});
console.log(await r.json());`,
    }),
  },
  {
    id: "me",
    method: "GET",
    path: "/v1/me",
    summary: "identity probe: tenant, roles, rate limit, auth mode",
    snippet: (origin, key) => ({
      curl: `curl ${origin}/api/v1/me \\
  -H "X-API-Key: ${ph(key)}"`,
      python: `import requests
r = requests.get("${origin}/api/v1/me", headers={"X-API-Key": "${ph(key)}"})
print(r.json())`,
      javascript: `const r = await fetch("${origin}/api/v1/me", {
  headers: { "X-API-Key": "${ph(key)}" },
});
console.log(await r.json());`,
    }),
  },
];

function methodColor(m: Endpoint["method"]): string {
  switch (m) {
    case "GET":
      return "text-emerald-400";
    case "POST":
      return "text-sky-400";
    case "DELETE":
      return "text-rose-400";
    case "PATCH":
      return "text-amber-400";
  }
}

export default function DevelopersPage() {
  const [key] = useApiKey();
  const [origin, setOrigin] = useState("http://127.0.0.1:7452");
  const [lang, setLang] = useState<Lang>("curl");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [probe, setProbe] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "ok"; status: number; body: string }
    | { kind: "error"; status: number; body: string }
  >({ kind: "idle" });

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  const snippets = useMemo(
    () => ENDPOINTS.map((e) => ({ ep: e, code: e.snippet(origin, key) })),
    [origin, key],
  );

  async function copy(id: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      window.setTimeout(
        () => setCopiedId((curr) => (curr === id ? null : curr)),
        1200,
      );
    } catch {
      /* ignore */
    }
  }

  async function tryMe(): Promise<void> {
    setProbe({ kind: "loading" });
    try {
      const headers: Record<string, string> = {};
      if (key) headers["X-API-Key"] = key;
      const r = await fetch("/api/v1/me", { headers });
      const body = await r.text();
      const pretty = (() => {
        try {
          return JSON.stringify(JSON.parse(body), null, 2);
        } catch {
          return body;
        }
      })();
      if (r.ok) setProbe({ kind: "ok", status: r.status, body: pretty });
      else setProbe({ kind: "error", status: r.status, body: pretty });
    } catch (e) {
      setProbe({
        kind: "error",
        status: 0,
        body: (e as Error).message || "network error",
      });
    }
  }

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8 sm:py-12 space-y-8">
      <header className="space-y-3">
        <div className="flex items-center gap-2 text-[var(--color-phosphor)]">
          <Code size={18} weight="duotone" />
          <span className="font-mono text-[11px] uppercase tracking-widest">
            developers
          </span>
        </div>
        <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">
          clawhum API, version 1
        </h1>
        <p className="text-sm text-[var(--color-muted)] max-w-2xl">
          Stable, version-pinned endpoints under{" "}
          <code className="font-mono text-[12px] px-1 py-0.5 bg-[var(--color-panel)] rounded">
            /v1
          </code>
          . Authenticate with the{" "}
          <code className="font-mono text-[12px] px-1 py-0.5 bg-[var(--color-panel)] rounded">
            X-API-Key
          </code>{" "}
          header. Every snippet below is pre-filled with your key when one is
          set in{" "}
          <Link href="/settings" className="underline hover:text-[var(--color-text)]">
            settings
          </Link>
          .
        </p>
      </header>

      <section className="border border-[var(--color-line)] rounded-md p-4 sm:p-5 bg-[var(--color-panel)] space-y-3">
        <div className="flex items-center gap-2 text-[var(--color-phosphor)]">
          <Key size={14} weight="duotone" />
          <span className="font-mono text-[10px] uppercase tracking-widest">
            your api key
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {key ? (
            <>
              <code className="font-mono text-[12px] px-2 py-1 bg-[var(--color-bg)] border border-[var(--color-line)] rounded">
                {maskKey(key)}
              </code>
              <button
                type="button"
                onClick={() => copy("key", key)}
                className="inline-flex items-center gap-1 text-xs text-[var(--color-muted)] hover:text-[var(--color-text)]"
                aria-label="copy api key"
              >
                {copiedId === "key" ? (
                  <Check size={12} weight="duotone" />
                ) : (
                  <Copy size={12} weight="duotone" />
                )}
                {copiedId === "key" ? "copied" : "copy"}
              </button>
            </>
          ) : (
            <span className="text-[var(--color-muted)]">
              no key set. snippets show{" "}
              <code className="font-mono text-[12px]">YOUR_API_KEY</code>.
            </span>
          )}
          <Link
            href="/settings"
            className="ml-auto inline-flex items-center gap-1 text-xs text-[var(--color-phosphor)] hover:underline"
          >
            <ShieldCheck size={12} weight="duotone" /> manage keys
          </Link>
        </div>
        <div className="pt-3 border-t border-[var(--color-line)] flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={tryMe}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono uppercase tracking-widest border border-[var(--color-line)] rounded hover:bg-[var(--color-bg)] disabled:opacity-50"
            disabled={probe.kind === "loading"}
          >
            <Terminal size={12} weight="duotone" />
            {probe.kind === "loading" ? "calling..." : "try /v1/me"}
          </button>
          {probe.kind !== "idle" && (
            <span
              className={`font-mono text-[10px] uppercase tracking-widest ${
                probe.kind === "ok"
                  ? "text-emerald-400"
                  : probe.kind === "error"
                    ? "text-rose-400"
                    : "text-[var(--color-muted)]"
              }`}
            >
              {probe.kind === "loading"
                ? "..."
                : `HTTP ${probe.status || "ERR"}`}
            </span>
          )}
        </div>
        {(probe.kind === "ok" || probe.kind === "error") && (
          <pre className="text-[11px] font-mono leading-relaxed overflow-x-auto bg-[var(--color-bg)] border border-[var(--color-line)] rounded p-3 max-h-56">
            {probe.body}
          </pre>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            endpoints
          </h2>
          <div
            role="tablist"
            aria-label="snippet language"
            className="inline-flex border border-[var(--color-line)] rounded overflow-hidden"
          >
            {(["curl", "python", "javascript"] as Lang[]).map((l) => (
              <button
                key={l}
                role="tab"
                aria-selected={lang === l}
                onClick={() => setLang(l)}
                className={`px-3 py-1 text-[10px] font-mono uppercase tracking-widest transition ${
                  lang === l
                    ? "bg-[var(--color-panel)] text-[var(--color-phosphor)]"
                    : "text-[var(--color-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>

        <ul className="space-y-4">
          {snippets.map(({ ep, code }) => (
            <li
              key={ep.id}
              className="border border-[var(--color-line)] rounded-md overflow-hidden"
            >
              <div className="px-4 py-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 bg-[var(--color-panel)] border-b border-[var(--color-line)]">
                <span
                  className={`font-mono text-[11px] font-semibold ${methodColor(ep.method)}`}
                >
                  {ep.method}
                </span>
                <code className="font-mono text-[12px] text-[var(--color-text)]">
                  {ep.path}
                </code>
                <span className="text-xs text-[var(--color-muted)] flex-1 min-w-[12rem]">
                  {ep.summary}
                </span>
              </div>
              {ep.body && (
                <div className="px-4 py-2 text-[11px] font-mono text-[var(--color-dim)] border-b border-[var(--color-line)]">
                  {ep.body}
                </div>
              )}
              <div className="relative">
                <pre className="text-[12px] font-mono leading-relaxed overflow-x-auto p-4 bg-[var(--color-bg)]">
                  {code[lang]}
                </pre>
                <button
                  type="button"
                  onClick={() => copy(ep.id, code[lang])}
                  className="absolute top-2 right-2 inline-flex items-center gap-1 px-2 py-1 text-[10px] font-mono uppercase tracking-widest border border-[var(--color-line)] rounded bg-[var(--color-panel)] hover:text-[var(--color-phosphor)]"
                  aria-label={`copy ${lang} snippet for ${ep.path}`}
                >
                  {copiedId === ep.id ? (
                    <Check size={12} weight="duotone" />
                  ) : (
                    <Copy size={12} weight="duotone" />
                  )}
                  {copiedId === ep.id ? "copied" : "copy"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="border-t border-[var(--color-line)] pt-6 text-sm text-[var(--color-muted)] space-y-2">
        <p>
          Authentication is the{" "}
          <code className="font-mono text-[11px]">X-API-Key</code> header on
          every request. Missing or unknown keys return{" "}
          <code className="font-mono text-[11px]">401</code>. Calls outside
          your role return{" "}
          <code className="font-mono text-[11px]">403</code>. Rate limit
          breaches return <code className="font-mono text-[11px]">429</code>{" "}
          with a <code className="font-mono text-[11px]">Retry-After</code>{" "}
          header.
        </p>
        <p className="flex flex-wrap items-center gap-2">
          <ArrowSquareOut size={12} weight="duotone" />
          <Link href="/usage" className="underline hover:text-[var(--color-text)]">
            check your current quota
          </Link>
          <span>·</span>
          <Link href="/webhooks" className="underline hover:text-[var(--color-text)]">
            manage webhooks
          </Link>
          <span>·</span>
          <Link href="/pricing" className="underline hover:text-[var(--color-text)]">
            plans and limits
          </Link>
        </p>
      </section>
    </main>
  );
}
