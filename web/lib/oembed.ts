// Helpers for the oEmbed discovery endpoint. Pure functions, easy to test.
//
// We accept any URL whose pathname matches /r/<id> on the same origin and
// return the share id. The id must be the canonical alnum + underscore +
// hyphen form the API produces; anything else is rejected so we don't echo
// arbitrary strings back to integrators.

export const SHARE_ID_RE = /^[A-Za-z0-9_-]{4,64}$/;

export interface ParsedShareUrl {
  id: string;
  origin: string;
}

/**
 * Extract a share id from a public /r/<id> URL. Returns null when the URL
 * is malformed, points at a different path, or carries an id that does not
 * match the canonical pattern. The optional `expectedOrigin` argument, when
 * supplied, additionally requires the URL to belong to that origin.
 */
export function parseShareUrl(
  raw: string,
  expectedOrigin?: string,
): ParsedShareUrl | null {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 2048) {
    return null;
  }
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (expectedOrigin && u.origin !== expectedOrigin) return null;
  // Accept exactly /r/<id> (no trailing path segments). Trailing slash OK.
  const parts = u.pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts.length !== 2 || parts[0] !== "r") return null;
  const id = parts[1];
  if (!SHARE_ID_RE.test(id)) return null;
  return { id, origin: u.origin };
}

export interface OEmbedRichResponse {
  version: "1.0";
  type: "rich";
  provider_name: string;
  provider_url: string;
  title: string;
  author_name?: string;
  html: string;
  width: number;
  height: number;
  cache_age: string;
  thumbnail_url?: string;
  thumbnail_width?: number;
  thumbnail_height?: number;
}

export interface BuildEmbedHtmlInput {
  origin: string;
  id: string;
  width: number;
  height: number;
}

/**
 * Build the iframe snippet integrators paste into their pages. The iframe
 * points at the public read-only /r/<id>/embed view, which renders without
 * the site chrome and is safe to load cross origin.
 */
export function buildEmbedHtml({ origin, id, width, height }: BuildEmbedHtmlInput): string {
  const src = `${origin}/r/${encodeURIComponent(id)}/embed`;
  // Attribute order is fixed so tests can assert on it.
  return (
    `<iframe src="${src}" width="${width}" height="${height}" ` +
    `frameborder="0" loading="lazy" allow="clipboard-write" ` +
    `referrerpolicy="no-referrer-when-downgrade" ` +
    `title="clawhum shared match ${id}" ` +
    `style="border:0;border-radius:2px;max-width:100%;"></iframe>`
  );
}

export interface ClampSizeOptions {
  defaultWidth: number;
  defaultHeight: number;
  minWidth: number;
  maxWidth: number;
  minHeight: number;
  maxHeight: number;
}

export const DEFAULT_SIZE: ClampSizeOptions = {
  defaultWidth: 480,
  defaultHeight: 360,
  minWidth: 240,
  maxWidth: 960,
  minHeight: 180,
  maxHeight: 720,
};

/**
 * Clamp caller-supplied maxwidth / maxheight (per oEmbed spec) to a sane
 * range. Non numeric input falls back to the defaults. Caller may pass
 * undefined for either dimension.
 */
export function clampSize(
  maxwidth: string | number | null | undefined,
  maxheight: string | number | null | undefined,
  opts: ClampSizeOptions = DEFAULT_SIZE,
): { width: number; height: number } {
  const w = parseDim(maxwidth, opts.defaultWidth);
  const h = parseDim(maxheight, opts.defaultHeight);
  return {
    width: Math.min(opts.maxWidth, Math.max(opts.minWidth, w)),
    height: Math.min(opts.maxHeight, Math.max(opts.minHeight, h)),
  };
}

function parseDim(v: string | number | null | undefined, fallback: number): number {
  if (v === null || v === undefined || v === "") return fallback;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.floor(n);
}
