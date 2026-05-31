import { NextRequest, NextResponse } from "next/server";

// We only care about the public embed surface for now. Keeping the
// matcher narrow avoids paying the upstream-fetch cost on every page.
export const config = {
  matcher: ["/r/:id/embed"],
};

const API_BASE = process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451";

/**
 * Per-workspace embed origin enforcement.
 *
 * Each share record carries the owning workspace's embed origin
 * allowlist. When that allowlist is non-empty we narrow the browser's
 * frame-ancestors CSP to exactly those origins, so a hostile site
 * cannot iframe a customer's share and trick their users.
 *
 * Empty allowlist (the default) keeps the existing wide-open embed
 * behaviour so nothing breaks on day zero.
 */
export async function middleware(req: NextRequest): Promise<NextResponse> {
  const res = NextResponse.next();
  res.headers.set("X-Content-Type-Options", "nosniff");

  const idMatch = req.nextUrl.pathname.match(/^\/r\/([^/]+)\/embed\/?$/);
  const id = idMatch?.[1];
  if (!id) return res;

  try {
    const r = await fetch(`${API_BASE}/share/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (!r.ok) return res;
    const data = (await r.json()) as { embed_allowed_origins?: string[] };
    const allowed = Array.isArray(data.embed_allowed_origins)
      ? data.embed_allowed_origins.filter((s) => typeof s === "string" && s.length > 0)
      : [];
    if (allowed.length > 0) {
      const frameAncestors = allowed.join(" ");
      res.headers.set(
        "Content-Security-Policy",
        `frame-ancestors ${frameAncestors}; default-src 'self' 'unsafe-inline' data:`,
      );
      // Legacy header for old IE/Edge versions that ignore CSP.
      // When the allowlist has exactly one entry we can be precise;
      // otherwise SAMEORIGIN is the safest fallback.
      res.headers.set(
        "X-Frame-Options",
        allowed.length === 1 ? "ALLOW-FROM " + allowed[0] : "SAMEORIGIN",
      );
    }
  } catch {
    // Fail open: if the API is unreachable from middleware the page
    // itself will render notFound() anyway, so we do not block here.
  }

  return res;
}
