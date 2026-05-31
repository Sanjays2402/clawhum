"use client";

/**
 * Batch matching UI.
 *
 * Lets a user drop or pick a single .zip of audio clips and runs the
 * whole archive through /batch in one shot. Shows progress states,
 * per-clip results, top match per clip, and download buttons for the
 * raw JSON or a flattened CSV.
 *
 * Design choices:
 * - One file picker, not a queue. The backend is the queue.
 * - Results render inline (table) so users can scan without leaving
 *   the page, and a "download CSV" button hits /batch again with
 *   format=csv so they get the canonical server-side file rather
 *   than a JS-stringified one.
 * - All empty / loading / error states are explicit. No spinners
 *   over content; we either show the dropzone or the result table.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import {
  FileZip,
  CloudArrowUp,
  CheckCircle,
  WarningCircle,
  DownloadSimple,
  Spinner,
  ArrowsClockwise,
} from "@phosphor-icons/react/dist/ssr";
import { API_BASE } from "@/lib/api";
import { toast } from "@/lib/toast";

interface BatchMatch {
  track_id: string;
  title: string;
  artist: string;
  score: number;
}

interface BatchRow {
  filename: string;
  error: string | null;
  elapsed_ms: number;
  matches: BatchMatch[];
}

interface BatchResponse {
  batch_id: string;
  count: number;
  ok: number;
  failed: number;
  results: BatchRow[];
}

const MAX_ZIP_MB = 200;

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MiB`;
}

export default function BatchPage() {
  const [file, setFile] = useState<File | null>(null);
  const [topK, setTopK] = useState<number>(3);
  const [dragOver, setDragOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BatchResponse | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const onPick = useCallback((f: File | null) => {
    setError(null);
    setResult(null);
    if (!f) {
      setFile(null);
      return;
    }
    const name = f.name.toLowerCase();
    if (!name.endsWith(".zip")) {
      setError("Please choose a .zip archive containing audio clips.");
      return;
    }
    if (f.size > MAX_ZIP_MB * 1024 * 1024) {
      setError(`Archive too large. Max ${MAX_ZIP_MB} MiB.`);
      return;
    }
    setFile(f);
  }, []);

  const runBatch = useCallback(
    async (format: "json" | "csv") => {
      if (!file) return;
      setLoading(true);
      setError(null);
      try {
        const fd = new FormData();
        fd.append("archive", file);
        fd.append("top_k", String(topK));
        fd.append("format", format);
        const r = await fetch(API_BASE + "/batch", { method: "POST", body: fd });
        if (!r.ok) {
          let detail = `request failed (${r.status})`;
          try {
            const j = await r.json();
            if (j?.detail) detail = String(j.detail);
          } catch {
            /* not json */
          }
          throw new Error(detail);
        }
        if (format === "csv") {
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `clawhum-batch-${Date.now()}.csv`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
          toast.success("batch csv downloaded", {
            description: `${file.name} / ${fmtBytes(file.size)}`,
          });
        } else {
          const j = (await r.json()) as BatchResponse;
          setResult(j);
          toast.success("batch complete", {
            description: `${j.ok}/${j.count} ok, ${j.failed} failed`,
          });
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        toast.error("batch failed", { description: msg.slice(0, 200) });
      } finally {
        setLoading(false);
      }
    },
    [file, topK],
  );

  const downloadJson = useCallback(() => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `clawhum-batch-${result.batch_id}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [result]);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const f = e.dataTransfer.files?.[0] ?? null;
      onPick(f);
    },
    [onPick],
  );

  const summary = useMemo(() => {
    if (!result) return null;
    const totalMs = result.results.reduce((s, r) => s + r.elapsed_ms, 0);
    return {
      total: result.count,
      ok: result.ok,
      failed: result.failed,
      totalMs,
      avgMs: result.ok > 0 ? Math.round(totalMs / result.ok) : 0,
    };
  }, [result]);

  return (
    <div className="px-4 sm:px-6 py-6 max-w-6xl mx-auto w-full">
      <header className="mb-6">
        <h1 className="font-mono text-[13px] uppercase tracking-widest text-[var(--color-phosphor)]">
          batch
        </h1>
        <p className="text-sm text-[var(--color-muted)] mt-1 max-w-2xl">
          Drop a .zip of hums or audio clips and get one results file back.
          Up to {MAX_ZIP_MB} MiB per archive and 100 clips per batch. Each
          clip runs through the same matcher as single shot match.
        </p>
      </header>

      {/* Dropzone + controls */}
      <section
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={`border border-dashed rounded-md p-6 sm:p-8 transition
          ${dragOver
            ? "border-[var(--color-phosphor)] bg-[var(--color-panel)]"
            : "border-[var(--color-line)] bg-[var(--color-bg)]"
          }`}
      >
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="flex items-center gap-3">
            <FileZip
              size={28}
              weight="duotone"
              className="text-[var(--color-phosphor)] shrink-0"
            />
            <div className="min-w-0">
              <div className="font-mono text-xs uppercase tracking-widest text-[var(--color-text)]">
                {file ? file.name : "drop zip here"}
              </div>
              <div className="text-[11px] text-[var(--color-dim)] mt-0.5">
                {file
                  ? `${fmtBytes(file.size)} ready to send`
                  : `or click to pick a file. accepted: .zip of .wav / .mp3 / .m4a / .flac / .ogg`}
              </div>
            </div>
          </div>
          <div className="sm:ml-auto flex flex-wrap items-center gap-2">
            <label className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
              top k
              <input
                type="number"
                min={1}
                max={10}
                value={topK}
                onChange={(e) =>
                  setTopK(Math.max(1, Math.min(10, Number(e.target.value) || 1)))
                }
                className="ml-2 w-14 bg-[var(--color-bg)] border border-[var(--color-line)] px-2 py-1 font-mono text-xs text-[var(--color-text)] focus:outline-none focus:border-[var(--color-phosphor)]"
              />
            </label>
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="inline-flex items-center gap-2 px-3 py-1.5 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]"
            >
              <CloudArrowUp size={14} weight="duotone" />
              choose file
            </button>
            <button
              type="button"
              disabled={!file || loading}
              onClick={() => runBatch("json")}
              className="inline-flex items-center gap-2 px-3 py-1.5 border border-[var(--color-phosphor)] bg-[var(--color-phosphor)] text-black font-mono text-[11px] uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {loading ? (
                <Spinner size={14} weight="duotone" className="animate-spin" />
              ) : (
                <ArrowsClockwise size={14} weight="duotone" />
              )}
              run batch
            </button>
            <input
              ref={inputRef}
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
            />
          </div>
        </div>
      </section>

      {error && (
        <div className="mt-4 flex items-start gap-2 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-text)]">
          <WarningCircle
            size={18}
            weight="duotone"
            className="text-amber-500 shrink-0 mt-0.5"
          />
          <div>
            <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
              error
            </div>
            <div className="text-sm">{error}</div>
          </div>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && !result && (
        <div className="mt-6 space-y-2" aria-busy="true" aria-live="polite">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-10 bg-[var(--color-panel)] border border-[var(--color-line)] animate-pulse"
            />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !result && !error && !file && (
        <div className="mt-6 border border-[var(--color-line)] px-4 py-8 text-center text-sm text-[var(--color-muted)]">
          <FileZip
            size={36}
            weight="duotone"
            className="text-[var(--color-dim)] mx-auto mb-2"
          />
          <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
            no batch yet
          </div>
          <div className="text-sm mt-1">
            Pick a .zip with one or more audio clips to get started.
          </div>
        </div>
      )}

      {/* Results */}
      {result && summary && (
        <section className="mt-6">
          <div className="flex flex-wrap items-center gap-4 mb-3">
            <div className="flex items-center gap-2">
              <CheckCircle
                size={18}
                weight="duotone"
                className="text-[var(--color-phosphor)]"
              />
              <span className="font-mono text-xs uppercase tracking-widest text-[var(--color-text)]">
                {summary.ok} matched / {summary.failed} failed / {summary.total} total
              </span>
            </div>
            <span className="font-mono text-[11px] text-[var(--color-dim)] uppercase tracking-widest">
              avg {summary.avgMs} ms / total {summary.totalMs} ms
            </span>
            <div className="ml-auto flex gap-2">
              <button
                type="button"
                onClick={() => runBatch("csv")}
                disabled={loading}
                className="inline-flex items-center gap-2 px-3 py-1.5 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]"
              >
                <DownloadSimple size={14} weight="duotone" />
                csv
              </button>
              <button
                type="button"
                onClick={downloadJson}
                className="inline-flex items-center gap-2 px-3 py-1.5 border border-[var(--color-line)] hover:border-[var(--color-phosphor)] font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]"
              >
                <DownloadSimple size={14} weight="duotone" />
                json
              </button>
            </div>
          </div>

          <div className="overflow-x-auto border border-[var(--color-line)]">
            <table className="w-full text-sm">
              <thead className="bg-[var(--color-panel)] text-[var(--color-muted)]">
                <tr>
                  <th className="text-left px-3 py-2 font-mono text-[10px] uppercase tracking-widest">
                    file
                  </th>
                  <th className="text-left px-3 py-2 font-mono text-[10px] uppercase tracking-widest">
                    top match
                  </th>
                  <th className="text-right px-3 py-2 font-mono text-[10px] uppercase tracking-widest">
                    score
                  </th>
                  <th className="text-right px-3 py-2 font-mono text-[10px] uppercase tracking-widest hidden sm:table-cell">
                    ms
                  </th>
                </tr>
              </thead>
              <tbody>
                {result.results.map((row) => {
                  const top = row.matches[0];
                  return (
                    <tr
                      key={row.filename}
                      className="border-t border-[var(--color-line)] hover:bg-[var(--color-panel)]"
                    >
                      <td className="px-3 py-2 font-mono text-[12px] text-[var(--color-text)] truncate max-w-[260px]">
                        {row.filename}
                      </td>
                      <td className="px-3 py-2 text-[var(--color-text)]">
                        {row.error ? (
                          <span className="inline-flex items-center gap-1 text-amber-500">
                            <WarningCircle size={14} weight="duotone" />
                            <span className="text-xs">{row.error}</span>
                          </span>
                        ) : top ? (
                          <span>
                            <span className="font-medium">{top.title || top.track_id}</span>
                            {top.artist && (
                              <span className="text-[var(--color-dim)]"> / {top.artist}</span>
                            )}
                          </span>
                        ) : (
                          <span className="text-[var(--color-dim)]">no matches above threshold</span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-[12px] text-right text-[var(--color-text)]">
                        {top ? top.score.toFixed(3) : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-right text-[var(--color-dim)] hidden sm:table-cell">
                        {row.elapsed_ms}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
