import { ImageResponse } from "next/og";

export const runtime = "nodejs";
export const alt = "clawhum shared match";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

interface SharedResult {
  track_id: string;
  title: string;
  artist: string;
  score: number;
}

interface SharedPayload {
  id: string;
  created_at: number;
  query_id: string;
  elapsed_ms: number;
  count: number;
  results: SharedResult[];
  filename: string | null;
  duration_sec: number | null;
  note: string | null;
}

async function fetchShare(id: string): Promise<SharedPayload | null> {
  const base = process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451";
  try {
    const r = await fetch(`${base}/share/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as SharedPayload;
  } catch {
    return null;
  }
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export default async function OgImage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const data = await fetchShare(id);

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
            shared match not found
          </div>
        </div>
      ),
      { ...size },
    );
  }

  const top = data.results[0];
  const others = data.results.slice(1, 4);

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
          <div style={{ display: "flex", color: accent }}>clawhum / match</div>
          <div style={{ display: "flex" }}>{data.count} candidates</div>
        </div>

        {/* top match */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            marginTop: 56,
            gap: 12,
          }}
        >
          <div style={{ fontSize: 28, color: dim, display: "flex" }}>
            top match
          </div>
          <div
            style={{
              fontSize: 64,
              fontWeight: 700,
              color: fg,
              display: "flex",
              lineHeight: 1.1,
            }}
          >
            {truncate(top?.title || "no match", 38)}
          </div>
          <div
            style={{
              fontSize: 36,
              color: dim,
              display: "flex",
              marginTop: 4,
            }}
          >
            {truncate(top?.artist || "unknown", 44)}
          </div>
          <div
            style={{
              fontSize: 30,
              color: accent,
              display: "flex",
              marginTop: 16,
            }}
          >
            score {top ? top.score.toFixed(3) : "0.000"}
            <span style={{ color: dim, marginLeft: 24 }}>
              {data.elapsed_ms} ms
            </span>
          </div>
        </div>

        {/* runners up */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            marginTop: "auto",
            gap: 8,
          }}
        >
          {others.length > 0 && (
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
              also
            </div>
          )}
          {others.map((r) => (
            <div
              key={r.track_id}
              style={{
                display: "flex",
                fontSize: 24,
                color: fg,
                justifyContent: "space-between",
              }}
            >
              <div style={{ display: "flex" }}>
                {truncate(`${r.title} / ${r.artist || "unknown"}`, 60)}
              </div>
              <div style={{ display: "flex", color: dim }}>
                {r.score.toFixed(3)}
              </div>
            </div>
          ))}
        </div>
      </div>
    ),
    { ...size },
  );
}
