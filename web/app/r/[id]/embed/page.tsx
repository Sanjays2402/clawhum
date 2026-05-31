import { notFound } from "next/navigation";
import { MusicNote } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";

interface SharedResult {
  track_id: string;
  title: string;
  artist?: string;
  album?: string;
  score: number;
  segment_index: number;
}

interface SharedPayload {
  id: string;
  created_at: number;
  elapsed_ms: number;
  count: number;
  results: SharedResult[];
  filename: string | null;
  duration_sec: number | null;
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

export const dynamic = "force-dynamic";

export async function generateMetadata(
  { params }: { params: Promise<{ id: string }> },
): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `clawhum embed ${id}`,
    robots: { index: false, follow: false },
  };
}

export default async function EmbedPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const data = await fetchShare(id);
  if (!data) notFound();

  const top = data.results[0];
  const top3 = data.results.slice(0, 3);
  const maxScore = data.results.reduce((m, r) => Math.max(m, r.score), 0) || 1;

  return (
    <div className="clawhum-embed-root">
      <style>{EMBED_CSS}</style>
      <a
          href={`/r/${encodeURIComponent(id)}`}
          target="_top"
          rel="noopener noreferrer"
          className="card"
          aria-label={`Open clawhum match ${id} in full view`}
        >
          <header className="head">
            <span className="dot" aria-hidden="true" />
            <span className="brand">clawhum / shared match</span>
            <span className="meta">{data.elapsed_ms} ms · {data.count} candidates</span>
          </header>

          {top ? (
            <div className="top">
              <MusicNote size={18} weight="duotone" className="topicon" />
              <div className="toptext">
                <div className="title">{top.title}</div>
                <div className="artist">{top.artist || "unknown"}</div>
              </div>
              <div className="score" aria-label={`Top score ${top.score.toFixed(4)}`}>
                {top.score.toFixed(3)}
              </div>
            </div>
          ) : (
            <div className="empty">no candidates were recorded for this query</div>
          )}

          {top3.length > 1 && (
            <ol className="rest">
              {top3.slice(1).map((r, i) => {
                const pct = Math.max(2, (r.score / maxScore) * 100);
                return (
                  <li key={`${r.track_id}-${r.segment_index}-${i}`} className="row">
                    <span className="rank">{String(i + 2).padStart(2, "0")}</span>
                    <span className="rowtitle" title={`${r.title} / ${r.artist || ""}`}>
                      {r.title}
                      <span className="rowartist">{r.artist ? ` · ${r.artist}` : ""}</span>
                    </span>
                    <span className="bar" aria-hidden="true">
                      <span className="fill" style={{ width: `${pct}%` }} />
                    </span>
                    <span className="rowscore">{r.score.toFixed(3)}</span>
                  </li>
                );
              })}
            </ol>
          )}

          <footer className="foot">
            <span>powered by clawhum</span>
            <span className="cta">view full match →</span>
          </footer>
      </a>
    </div>
  );
}

const EMBED_CSS = `
  :root { color-scheme: dark light; }
  /* Cover the site chrome rendered by the root layout. The embed page is
     loaded inside customer iframes; the parent page never sees nav / footer. */
  body { background: transparent !important; }
  body > div:first-child > header,
  body > div:first-child > nav,
  body > div:first-child > footer,
  body > div:first-child > [role="banner"],
  body > div:first-child > [data-site-nav],
  body > div:first-child > [data-transport-bar] { display: none !important; }
  .clawhum-embed-root {
    position: fixed; inset: 0; z-index: 2147483000;
    display: flex; align-items: stretch; justify-content: stretch;
    background: transparent;
    overflow: auto;
    --bg: #0b0c0d;
    --line: #1f2326;
    --text: #e6e8e6;
    --dim: #5b6166;
    --muted: #8a9098;
    --phos: #7cffb2;
    --mag: #ff6ab8;
    font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace;
    padding: 8px;
  }
  @media (prefers-color-scheme: light) {
    .clawhum-embed-root { --bg:#fafafa; --line:#e5e7e6; --text:#0b0c0d; --dim:#9aa0a6; --muted:#5b6166; --phos:#0a8a4a; --mag:#c1267d; }
  }
  .card {
    display: block;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 2px;
    padding: 10px 12px;
    text-decoration: none;
    color: var(--text);
    width: 100%;
    max-width: 100%;
    align-self: flex-start;
    box-sizing: border-box;
  }
  .card:hover { border-color: var(--phos); }
  .head {
    display: flex; align-items: center; gap: 8px;
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;
    color: var(--dim);
  }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--phos); box-shadow: 0 0 6px var(--phos); }
  .brand { color: var(--muted); }
  .meta { margin-left: auto; }
  .top {
    display: grid; grid-template-columns: auto 1fr auto; align-items: center;
    gap: 10px; margin-top: 10px; padding-bottom: 8px;
    border-bottom: 1px solid var(--line);
  }
  .topicon { color: var(--phos); }
  .toptext { min-width: 0; }
  .title { font-size: 14px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .artist { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .score { font-size: 18px; color: var(--phos); font-variant-numeric: tabular-nums; }
  .empty { margin-top: 10px; padding: 8px 0; font-size: 12px; color: var(--muted); }
  .rest { list-style: none; margin: 8px 0 0; padding: 0; }
  .row {
    display: grid; grid-template-columns: 24px 1fr 60px 44px;
    align-items: center; gap: 8px; padding: 4px 0;
    font-size: 11px;
  }
  .rank { color: var(--dim); font-variant-numeric: tabular-nums; }
  .rowtitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
  .rowartist { color: var(--muted); }
  .bar { display: block; height: 4px; background: var(--line); border-radius: 1px; overflow: hidden; }
  .fill { display: block; height: 100%; background: linear-gradient(90deg, var(--mag), var(--phos)); }
  .rowscore { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
  .foot {
    display: flex; align-items: center; justify-content: space-between;
    margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line);
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--dim);
  }
  .cta { color: var(--phos); }
`;
