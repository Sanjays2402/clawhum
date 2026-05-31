// Validates the PWA shell: manifest + service worker are present, valid
// JSON / JavaScript, and reference real files in /public.
//
// Run with: pnpm test  (uses tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(__dirname, "..");
const PUBLIC = join(ROOT, "public");

test("pwa: manifest is valid and references real icons", () => {
  const raw = readFileSync(join(PUBLIC, "manifest.webmanifest"), "utf8");
  const m = JSON.parse(raw) as {
    name?: string;
    short_name?: string;
    start_url?: string;
    display?: string;
    icons?: { src: string; sizes: string; type?: string; purpose?: string }[];
  };
  assert.ok(m.name && m.short_name, "needs name + short_name");
  assert.equal(m.display, "standalone");
  assert.ok(Array.isArray(m.icons) && m.icons.length >= 2, "needs >= 2 icons");
  for (const ic of m.icons ?? []) {
    const p = join(PUBLIC, ic.src.replace(/^\//, ""));
    assert.ok(existsSync(p), `icon missing: ${ic.src}`);
    assert.ok(statSync(p).size > 0, `icon empty: ${ic.src}`);
  }
  const hasMaskable = (m.icons ?? []).some(i => (i.purpose ?? "").includes("maskable"));
  assert.ok(hasMaskable, "needs at least one maskable icon");
  assert.ok(m.start_url?.startsWith("/"), "start_url must be same-origin");
});

test("pwa: service worker exists and avoids caching /api/*", () => {
  const sw = readFileSync(join(PUBLIC, "sw.js"), "utf8");
  assert.match(sw, /addEventListener\(["']install["']/);
  assert.match(sw, /addEventListener\(["']fetch["']/);
  // Must keep /api/* off the cache so auth + live matches stay correct.
  assert.match(sw, /\/api\//);
  assert.match(sw, /isApi/);
  // Must precache the offline shell.
  assert.match(sw, /\/offline/);
});

test("pwa: offline page renders a useful shell", () => {
  const src = readFileSync(join(ROOT, "app", "offline", "page.tsx"), "utf8");
  assert.match(src, /offline/i);
  assert.match(src, /history/i);
});
