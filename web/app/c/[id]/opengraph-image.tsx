import { ImageResponse } from "next/og";

export const runtime = "nodejs";
export const alt = "clawhum shared collection";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

interface PublicResult {
  track_id: string;
  title: string;
  artist?: string;
  score: number;
}

interface PublicItem {
  label: string;
  results: PublicResult[];
  filename: string | null;
  duration_sec: number | null;
}

interface PublicCollection {
  id: string;
  created_at: number;
  updated_at: number;
  title: string;
  note: string | null;
  items: PublicItem[];
}

export async function fetchCollectionForOg(
  id: string,
  fetcher: typeof fetch = fetch,
): Promise<PublicCollection | null> {
  const base = process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451";
  try {
    const r = await fetcher(`${base}/collections/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as PublicCollection;
  } catch {
    return null;
  }
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "\u2026" : s;
}

export default async function OgImage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const data = await fetchCollectionForOg(id);

  const bg = "#0a0a0a";
  const fg = "#fafafa";
  const dim = "#8a8a8a";
  const accent = "#22d3ee";

  if (!data) {
    return new ImageResponse(
      (
        <div
          style={{
            width: "100%",
            height: "100%",
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
            background: bg,
            color: fg,
            fontFamily: "monospace",
          }}
        >
          <div style={{ fontSize: 96, color: accent }}>clawhum</div>
          <div style={{ fontSize: 32, color: dim, marginTop: 24 }}>
            collection not found
          </div>
        </div>
      ),
      { ...size },
    );
  }

  // Top item: prefer the first item with a top result.
  const topItem = data.items.find((it) => it.results && it.results.length > 0);
  const topResult = topItem?.results?.[0];
  const previewItems = data.items.slice(0, 4);
  const totalResults = data.items.reduce(
    (n, it) => n + (it.results ? it.results.length : 0),
    0,
  );

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          background: bg,
          color: fg,
          fontFamily: "monospace",
          padding: 64,
        }}
      >
        {/* header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontSize: 22,
            color: dim,
            textTransform: "uppercase",
            letterSpacing: 4,
          }}
        >
          <div style={{ display: "flex", color: accent }}>
            clawhum / collection
          </div>
          <div style={{ display: "flex" }}>
            {data.items.length} item{data.items.length === 1 ? "" : "s"}
            <span style={{ marginLeft: 18, color: dim }}>
              {totalResults} match{totalResults === 1 ? "" : "es"}
            </span>
          </div>
        </div>

        {/* title block */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            marginTop: 48,
            gap: 14,
          }}
        >
          <div
            style={{
              fontSize: 64,
              fontWeight: 700,
              color: fg,
              display: "flex",
              lineHeight: 1.1,
            }}
          >
            {truncate(data.title || "untitled collection", 38)}
          </div>
          {data.note && (
            <div
              style={{
                fontSize: 26,
                color: dim,
                display: "flex",
                lineHeight: 1.3,
              }}
            >
              {truncate(data.note, 90)}
            </div>
          )}
          {topResult && (
            <div
              style={{
                fontSize: 28,
                color: accent,
                display: "flex",
                marginTop: 10,
              }}
            >
              top {truncate(topResult.title, 30)}
              <span style={{ color: dim, marginLeft: 18 }}>
                {truncate(topResult.artist || "unknown", 28)}
              </span>
            </div>
          )}
        </div>

        {/* item preview list */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            marginTop: "auto",
            gap: 8,
          }}
        >
          <div
            style={{
              fontSize: 20,
              color: dim,
              display: "flex",
              textTransform: "uppercase",
              letterSpacing: 3,
              marginBottom: 6,
            }}
          >
            items
          </div>
          {previewItems.map((it, idx) => {
            const best = it.results?.[0];
            return (
              <div
                key={idx}
                style={{
                  display: "flex",
                  fontSize: 22,
                  color: fg,
                  justifyContent: "space-between",
                }}
              >
                <div style={{ display: "flex" }}>
                  {truncate(it.label || it.filename || `item ${idx + 1}`, 48)}
                </div>
                <div style={{ display: "flex", color: dim }}>
                  {best
                    ? `${truncate(best.title, 24)} \u00b7 ${best.score.toFixed(3)}`
                    : "no match"}
                </div>
              </div>
            );
          })}
          {data.items.length > previewItems.length && (
            <div
              style={{
                display: "flex",
                fontSize: 18,
                color: dim,
                marginTop: 4,
              }}
            >
              +{data.items.length - previewItems.length} more
            </div>
          )}
        </div>
      </div>
    ),
    { ...size },
  );
}
