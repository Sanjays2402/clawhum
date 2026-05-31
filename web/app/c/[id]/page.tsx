import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Folders, MusicNote, Clock } from "@phosphor-icons/react/dist/ssr";

interface PublicResult {
  track_id: string;
  title: string;
  artist?: string;
  album?: string;
  score: number;
  segment_index: number;
}

interface PublicItem {
  label: string;
  results: PublicResult[];
  query_id: string | null;
  elapsed_ms: number;
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

async function fetchCollection(id: string): Promise<PublicCollection | null> {
  const base = process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451";
  try {
    const r = await fetch(`${base}/collections/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (r.status === 404) return null;
    if (!r.ok) return null;
    return (await r.json()) as PublicCollection;
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
  const data = await fetchCollection(id);
  if (!data) return { title: "clawhum / collection not found" };
  const desc = data.note
    ? data.note
    : `A clawhum collection of ${data.items.length} ranked match${data.items.length === 1 ? "" : "es"}.`;
  const title = `${data.title} / clawhum collection`;
  return {
    title,
    description: desc,
    openGraph: { title, description: desc, type: "article" },
    twitter: { card: "summary_large_image", title, description: desc },
  };
}

export default async function PublicCollectionPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const data = await fetchCollection(id);
  if (!data) notFound();

  return (
    <div className="px-4 py-6 max-w-3xl mx-auto space-y-5">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <Folders size={12} weight="duotone" />
          <span>collection</span>
          <span aria-hidden="true">/</span>
          <span className="text-[var(--color-phosphor)]">{data.id}</span>
        </div>
        <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-text)]">
          {data.title}
        </h1>
        {data.note && (
          <p className="font-mono text-[11px] text-[var(--color-muted)]">{data.note}</p>
        )}
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
          <span>created {fmtDate(data.created_at)}</span>
          {data.updated_at && data.updated_at !== data.created_at && (
            <span>updated {fmtDate(data.updated_at)}</span>
          )}
          <span>
            <MusicNote size={11} weight="duotone" className="inline mr-1 -mt-0.5" />
            {data.items.length} item{data.items.length === 1 ? "" : "s"}
          </span>
        </div>
      </header>

      {data.items.length === 0 ? (
        <section className="panel rounded-[2px] px-4 py-10 text-center font-mono text-[11px] text-[var(--color-muted)]">
          this collection has no items yet.
        </section>
      ) : (
        <ol className="space-y-4">
          {data.items.map((item, idx) => {
            const top = item.results[0];
            const maxScore = item.results.reduce((m, r) => Math.max(m, r.score), 0) || 1;
            return (
              <li key={`${idx}-${item.query_id || ""}`} className="panel rounded-[2px]">
                <div className="px-3 py-2 border-b border-[var(--color-line)] flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono text-[10px] text-[var(--color-dim)] tabular-nums">
                      {String(idx + 1).padStart(2, "0")}
                    </span>
                    <span className="font-mono text-[12px] text-[var(--color-text)] truncate">
                      {item.label || top?.title || "untitled item"}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest hidden sm:inline">
                    <Clock size={11} weight="duotone" className="inline mr-1 -mt-0.5" />
                    {item.elapsed_ms} ms
                  </span>
                </div>
                {item.results.length === 0 ? (
                  <div className="px-3 py-4 font-mono text-[11px] text-[var(--color-muted)]">
                    no candidates were recorded.
                  </div>
                ) : (
                  <ol className="divide-y divide-[var(--color-line)]">
                    {item.results.map((r, i) => {
                      const pct = Math.max(2, (r.score / maxScore) * 100);
                      return (
                        <li
                          key={`${r.track_id}-${r.segment_index}-${i}`}
                          className="px-3 py-2 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3"
                        >
                          <div className="flex items-center gap-3 min-w-0 sm:w-1/2">
                            <span className="font-mono text-[10px] text-[var(--color-dim)] tabular-nums w-5">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            <MusicNote
                              size={13}
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
              </li>
            );
          })}
        </ol>
      )}

      <footer className="flex items-center justify-between pt-3 border-t border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
        <span>read-only / anyone with the link can view</span>
        <Link href="/" className="hover:text-[var(--color-phosphor)]">
          try clawhum →
        </Link>
      </footer>
    </div>
  );
}
