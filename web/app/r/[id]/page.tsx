import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ShareNetwork, MusicNote, Clock } from "@phosphor-icons/react/dist/ssr";
import EmbedSnippet from "@/components/EmbedSnippet";

interface SharedResult {
  track_id: string;
  title: string;
  artist: string;
  album?: string;
  score: number;
  segment_index: number;
  preview_url?: string | null;
  artwork_url?: string | null;
  source: string;
  tempo_bpm?: number | null;
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
    if (r.status === 404) return null;
    if (!r.ok) return null;
    return (await r.json()) as SharedPayload;
  } catch {
    return null;
  }
}

function fmtDate(epochSec: number): string {
  if (!epochSec) return "";
  const d = new Date(epochSec * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())} UTC`;
}

export async function generateMetadata(
  { params }: { params: Promise<{ id: string }> },
): Promise<Metadata> {
  const { id } = await params;
  const data = await fetchShare(id);
  if (!data) {
    return { title: "clawhum / shared match not found" };
  }
  const top = data.results[0];
  const title = top
    ? `${top.title} by ${top.artist || "unknown"} / clawhum match`
    : "clawhum / shared match";
  const desc = top
    ? `Top match scored ${top.score.toFixed(3)} across ${data.count} candidates in ${data.elapsed_ms} ms.`
    : `Shared clawhum match result with ${data.count} candidates.`;
  return {
    title,
    description: desc,
    openGraph: { title, description: desc, type: "article" },
    twitter: { card: "summary_large_image", title, description: desc },
    other: {
      // oEmbed discovery: lets WordPress, Notion, Slack, etc. auto-render
      // this share when its URL is pasted into supported editors.
      "oembed:json": `/api/oembed?url=${encodeURIComponent(`/r/${id}`)}&format=json`,
    },
    alternates: {
      types: {
        "application/json+oembed": `/api/oembed?url=${encodeURIComponent(`/r/${id}`)}&format=json`,
      },
    },
  };
}

export default async function SharedMatchPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const data = await fetchShare(id);
  if (!data) notFound();

  const top = data.results[0];
  const maxScore = data.results.reduce((m, r) => Math.max(m, r.score), 0) || 1;
  const dur = data.duration_sec ?? 0;

  return (
    <div className="px-4 py-6 max-w-3xl mx-auto space-y-5">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <ShareNetwork size={12} weight="duotone" />
          <span>shared match</span>
          <span aria-hidden="true">/</span>
          <span className="text-[var(--color-phosphor)]">{data.id}</span>
        </div>
        <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-text)]">
          {top?.title || "no candidates"}
          {top?.artist ? (
            <span className="text-[var(--color-muted)]"> by {top.artist}</span>
          ) : null}
        </h1>
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
          <span>shared {fmtDate(data.created_at)}</span>
          <span>
            <Clock size={11} weight="duotone" className="inline mr-1 -mt-0.5" />
            {data.elapsed_ms} ms
          </span>
          <span>candidates {data.count}</span>
          {dur > 0 && <span>window {dur.toFixed(2)}s</span>}
          {data.filename && <span>file {data.filename}</span>}
        </div>
      </header>

      <section className="panel rounded-[2px]">
        <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between">
          <span className="label-xs">ranked candidates</span>
          <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
            score 0 to {maxScore.toFixed(3)}
          </span>
        </div>
        {data.results.length === 0 ? (
          <div className="px-4 py-8 font-mono text-xs text-[var(--color-muted)]">
            no matches were recorded for this query.
          </div>
        ) : (
          <ol className="divide-y divide-[var(--color-line)]">
            {data.results.map((r, i) => {
              const pct = Math.max(2, (r.score / maxScore) * 100);
              return (
                <li
                  key={`${r.track_id}-${r.segment_index}-${i}`}
                  className="px-3 py-2.5 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3"
                >
                  <div className="flex items-center gap-3 min-w-0 sm:w-1/2">
                    <span className="font-mono text-[10px] text-[var(--color-dim)] tabular-nums w-5">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <MusicNote
                      size={14}
                      weight="duotone"
                      className="text-[var(--color-phosphor)] shrink-0"
                    />
                    <div className="min-w-0">
                      <div className="font-mono text-[12px] text-[var(--color-text)] truncate">
                        {r.title}
                      </div>
                      <div className="font-mono text-[10px] text-[var(--color-muted)] truncate uppercase tracking-widest">
                        {r.artist || "unknown"}
                        {r.album ? ` / ${r.album}` : ""}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 sm:flex-1">
                    <div className="meter-track h-2 rounded-[1px] flex-1">
                      <div
                        className="meter-fill"
                        style={{
                          width: `${pct}%`,
                          background:
                            i === 0
                              ? "linear-gradient(90deg, var(--color-magenta), var(--color-phosphor))"
                              : undefined,
                        }}
                      />
                    </div>
                    <span className="font-mono text-[11px] tabular-nums text-[var(--color-phosphor)] w-16 text-right">
                      {r.score.toFixed(4)}
                    </span>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <EmbedSnippet shareId={data.id} />

      <footer className="flex items-center justify-between pt-2 border-t border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
        <span>read-only / anyone with the link can view</span>
        <Link href="/" className="hover:text-[var(--color-phosphor)]">
          try clawhum →
        </Link>
      </footer>
    </div>
  );
}
