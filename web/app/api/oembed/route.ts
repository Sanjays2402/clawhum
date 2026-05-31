import { NextRequest, NextResponse } from "next/server";
import {
  buildEmbedHtml,
  clampSize,
  DEFAULT_SIZE,
  parseShareUrl,
  type OEmbedRichResponse,
} from "@/lib/oembed";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

interface SharedResult {
  track_id: string;
  title: string;
  artist?: string;
  score: number;
}

interface SharedPayload {
  id: string;
  created_at: number;
  count: number;
  results: SharedResult[];
  embed_allowed_origins?: string[];
}

const API_BASE = process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451";

async function fetchShare(id: string): Promise<SharedPayload | null> {
  try {
    const r = await fetch(`${API_BASE}/share/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as SharedPayload;
  } catch {
    return null;
  }
}

function jsonError(status: number, message: string): NextResponse {
  return new NextResponse(JSON.stringify({ error: message }), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function originFromRequest(req: NextRequest): string {
  // Trust the incoming request's URL so dev (localhost:7452) and prod
  // (custom domain) both produce correct embed src URLs without env config.
  const u = new URL(req.url);
  return `${u.protocol}//${u.host}`;
}

/**
 * GET /api/oembed?url=<share url>&format=json&maxwidth=&maxheight=
 *
 * Returns an oEmbed 1.0 rich response for any public /r/<id> share page.
 * Discoverable via the <link rel="alternate" type="application/json+oembed">
 * tag we ship on the share page itself.
 */
export async function GET(req: NextRequest): Promise<NextResponse> {
  const sp = req.nextUrl.searchParams;
  const format = (sp.get("format") || "json").toLowerCase();
  if (format !== "json") {
    return jsonError(501, "only format=json is supported");
  }

  const raw = sp.get("url");
  if (!raw) {
    return jsonError(400, "missing url parameter");
  }

  const origin = originFromRequest(req);
  const parsed = parseShareUrl(raw, origin);
  if (!parsed) {
    return jsonError(404, "url does not point to a clawhum share");
  }

  const data = await fetchShare(parsed.id);
  if (!data) {
    return jsonError(404, "shared match not found");
  }

  // Per-workspace embed origin allowlist enforcement. When the
  // workspace that owns this share has registered one or more allowed
  // origins, browser oEmbed calls from any other origin are rejected
  // with 403, and the response's Access-Control-Allow-Origin is
  // narrowed to the calling origin (only when allowed) rather than
  // the wildcard. Server-to-server calls (no Origin header) are still
  // served so link previews and crawlers keep working.
  const allowed = Array.isArray(data.embed_allowed_origins)
    ? data.embed_allowed_origins
    : [];
  const callerOrigin = req.headers.get("origin");
  let corsOrigin = "*";
  if (allowed.length > 0) {
    if (callerOrigin) {
      if (!allowed.includes(callerOrigin)) {
        return jsonError(403, "origin not permitted to embed this share");
      }
      corsOrigin = callerOrigin;
    } else {
      // No browser caller. Strip the permissive ACAO so a hostile
      // page cannot replay this response from JS.
      corsOrigin = allowed[0]!;
    }
  }

  const { width, height } = clampSize(sp.get("maxwidth"), sp.get("maxheight"), DEFAULT_SIZE);
  const top = data.results?.[0];
  const title = top
    ? `${top.title}${top.artist ? ` by ${top.artist}` : ""} on clawhum`
    : `clawhum shared match ${data.id}`;

  const body: OEmbedRichResponse = {
    version: "1.0",
    type: "rich",
    provider_name: "clawhum",
    provider_url: origin,
    title,
    author_name: top?.artist || undefined,
    html: buildEmbedHtml({ origin, id: parsed.id, width, height }),
    width,
    height,
    cache_age: "3600",
    thumbnail_url: `${origin}/r/${encodeURIComponent(parsed.id)}/opengraph-image`,
    thumbnail_width: 1200,
    thumbnail_height: 630,
  };

  return new NextResponse(JSON.stringify(body), {
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=300, s-maxage=3600",
      "Access-Control-Allow-Origin": corsOrigin,
      Vary: "Origin",
    },
  });
}
