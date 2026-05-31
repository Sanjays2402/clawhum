"use client";

import { useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useParams } from "next/navigation";
import Spectrogram from "@/components/Spectrogram";
import { swrFetcher } from "@/lib/api";
import { ArrowLeft, MusicNotes, WaveSawtooth, SpeakerHigh } from "@phosphor-icons/react";

type TrackSummary = {
  id: string;
  title: string;
  artist: string;
  album: string;
  duration_s: number;
  source: string;
  tempo_bpm: number | null;
  key: string | null;
  preview_url: string | null;
  artwork_url: string | null;
  has_audio: boolean;
};

export default function TrackDetailPage() {
  const params = useParams<{ id: string }>();
  const id = useMemo(() => {
    const raw = params?.id;
    if (!raw) return "";
    return Array.isArray(raw) ? raw[0] : raw;
  }, [params]);

  const { data: t, error, isLoading } = useSWR<TrackSummary>(
    id ? `/api/track/${encodeURIComponent(id)}` : null,
    swrFetcher,
  );

  const audioSrc = t?.has_audio ? `/api/track/${encodeURIComponent(id)}/audio` : t?.preview_url || null;

  return (
    <div className="px-4 py-4 max-w-4xl mx-auto">
      <Link
        href="/catalog"
        className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] mb-3"
      >
        <ArrowLeft size={12} weight="duotone" /> catalog
      </Link>

      {error ? (
        <div className="panel-inset rounded-[2px] py-12 text-center font-mono text-xs text-[var(--color-red,#ff6b6b)]">
          {(error as any)?.status === 404
            ? <>track not found in current index</>
            : <>failed to load track / {String((error as any)?.message || error)}</>
          }
          <div className="mt-3">
            <Link href="/catalog" className="text-[var(--color-phosphor)] hover:underline">→ back to catalog</Link>
          </div>
        </div>
      ) : isLoading || !t ? (
        <div className="panel rounded-[2px] p-6 animate-pulse">
          <div className="flex items-start gap-4">
            <div className="w-24 h-24 panel-inset rounded-[2px]" />
            <div className="flex-1 space-y-3">
              <div className="h-5 bg-[var(--color-line)] rounded-[1px] w-2/3" />
              <div className="h-3 bg-[var(--color-line)] rounded-[1px] w-1/2" />
              <div className="h-3 bg-[var(--color-line)] rounded-[1px] w-1/3" />
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="panel rounded-[2px] p-4 sm:p-6">
            <div className="flex flex-col sm:flex-row items-start gap-4">
              {t.artwork_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={t.artwork_url}
                  alt=""
                  className="w-24 h-24 rounded-[2px] object-cover border border-[var(--color-line)]"
                />
              ) : (
                <div className="w-24 h-24 panel-inset rounded-[2px] flex items-center justify-center text-[var(--color-muted)]">
                  <MusicNotes size={32} weight="duotone" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="text-lg sm:text-xl break-words">
                  {t.title || <span className="text-[var(--color-dim)]">untitled</span>}
                </div>
                <div className="font-mono text-[12px] text-[var(--color-muted)] mt-0.5 break-words">
                  {t.artist || "unknown artist"}{t.album ? ` / ${t.album}` : ""}
                </div>
                <div className="font-mono text-[10px] text-[var(--color-dim)] mt-1 break-all">{t.id}</div>
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-4 font-mono text-[11px]">
              <Field k="source" v={t.source || "—"} />
              <Field k="duration" v={t.duration_s ? `${t.duration_s.toFixed(1)}s` : "—"} />
              <Field k="tempo" v={t.tempo_bpm ? `${t.tempo_bpm.toFixed(0)} bpm` : "—"} accent />
              <Field k="key" v={t.key || "—"} />
            </div>

            <div className="mt-5">
              <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] mb-1">
                <WaveSawtooth size={12} weight="duotone" /> reference signature
              </div>
              <div className="panel-inset rounded-[2px] overflow-hidden">
                <Spectrogram height={80} seed={t.id} />
              </div>
            </div>

            {audioSrc ? (
              <div className="mt-4">
                <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] mb-1">
                  <SpeakerHigh size={12} weight="duotone" /> {t.has_audio ? "reference audio" : "preview"}
                </div>
                <audio controls preload="metadata" src={audioSrc} className="w-full h-9" />
              </div>
            ) : (
              <div className="mt-4 font-mono text-[10px] text-[var(--color-dim)]">no playable audio for this track</div>
            )}

            <div className="mt-5 flex flex-wrap gap-2 font-mono text-[10px] uppercase tracking-widest">
              <Link
                href={`/?prefill=${encodeURIComponent(t.id)}`}
                className="px-3 py-1.5 border border-[var(--color-phosphor-dim)] text-[var(--color-phosphor)] rounded-[2px] hover:bg-[var(--color-phosphor)] hover:text-[#04140A] transition"
              >
                hum to match
              </Link>
              <Link
                href="/catalog"
                className="px-3 py-1.5 border border-[var(--color-line-2)] text-[var(--color-muted)] rounded-[2px] hover:text-[var(--color-phosphor)] transition"
              >
                back to catalog
              </Link>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Field({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="panel-inset px-2 py-1.5 rounded-[1px]">
      <div className="text-[9px] uppercase tracking-widest text-[var(--color-dim)]">{k}</div>
      <div className={`tabular-nums truncate ${accent ? "text-[var(--color-phosphor)]" : "text-[var(--color-text)]"}`}>{v}</div>
    </div>
  );
}
