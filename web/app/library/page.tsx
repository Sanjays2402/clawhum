"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcher, type Health, type Stats } from "@/lib/api";

export default function LibraryPage() {
  const { data: stats, mutate: refreshStats, isLoading: statsLoading, error: statsErr } = useSWR<Stats>("/api/stats", swrFetcher, { refreshInterval: 5000 });
  const { data: health } = useSWR<Health>("/api/health", swrFetcher, { refreshInterval: 5000 });

  const [path, setPath] = useState("");
  const [playlist, setPlaylist] = useState("");
  const [useClap, setUseClap] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function reindex() {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch("/api/reindex", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          library_path: path || null,
          spotify_playlist: playlist || null,
          use_clap: useClap,
        }),
      });
      const j = await r.json();
      if (!r.ok) {
        setMsg({ ok: false, text: typeof j === "string" ? j : JSON.stringify(j) });
      } else {
        setMsg({ ok: true, text: `reindex queued / library=${j.library_path || "default"} / spotify=${j.spotify_playlist || "—"}` });
        setTimeout(() => refreshStats(), 1500);
      }
    } catch (e: any) {
      setMsg({ ok: false, text: e?.message || String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="px-4 py-4 space-y-4">
      <div>
        <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)]">
          index / inspection + rebuild
        </h1>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <StatTile label="tracks" value={stats?.tracks} loading={statsLoading} />
        <StatTile label="vectors" value={stats?.vectors} loading={statsLoading} accent />
        <StatTile label="dim" value={stats?.dim} loading={statsLoading} />
        <StatTile label="backend" value={stats?.backend} loading={statsLoading} mono />
        <StatTile label="embedder" value={health?.embedder} loading={!health} mono />
      </div>

      {statsErr && (
        <div className="border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] px-3 py-2 text-[var(--color-amber)] font-mono text-xs">
          stats fetch failed / {String((statsErr as any).message || statsErr)}
        </div>
      )}

      {/* Health */}
      <div className="panel rounded-[2px] p-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className="label-xs">health</span>
          <span className={`font-mono text-[10px] uppercase tracking-widest ${health?.ok ? "text-[var(--color-phosphor)]" : "text-[var(--color-amber)]"}`}>
            {health?.ok ? "ok" : "unreachable"}
          </span>
        </div>
        {health && (
          <div className="font-mono text-[11px] grid grid-cols-2 md:grid-cols-4 gap-2 text-[var(--color-muted)]">
            <span>version <span className="text-[var(--color-text)]">{health.version}</span></span>
            <span>embedder <span className="text-[var(--color-text)]">{health.embedder}</span></span>
            <span>backend <span className="text-[var(--color-text)]">{health.index_backend}</span></span>
            <span>vectors <span className="text-[var(--color-text)] tabular-nums">{health.vectors}</span></span>
          </div>
        )}
      </div>

      {/* Reindex form */}
      <div className="panel rounded-[2px] p-4 space-y-3">
        <div className="label-xs">rebuild index</div>
        <p className="font-mono text-[10px] text-[var(--color-dim)]">
          point at a local audio directory or a spotify playlist id. enable clap to use the neural embedder.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="block">
            <span className="label-xs block mb-1">library_path</span>
            <input
              value={path}
              onChange={e => setPath(e.target.value)}
              placeholder="/abs/path/to/audio (blank = default)"
              className="w-full"
            />
          </label>
          <label className="block">
            <span className="label-xs block mb-1">spotify_playlist</span>
            <input
              value={playlist}
              onChange={e => setPlaylist(e.target.value)}
              placeholder="playlist id (optional)"
              className="w-full"
            />
          </label>
        </div>
        <label className="flex items-center gap-2 font-mono text-[11px] text-[var(--color-muted)]">
          <input type="checkbox" checked={useClap} onChange={e => setUseClap(e.target.checked)} className="w-3 h-3" />
          use_clap / neural embedder
        </label>
        <div className="flex items-center gap-3">
          <button
            onClick={reindex}
            disabled={busy}
            className="btn-primary px-4 py-2 rounded-[2px] font-mono text-[12px] uppercase tracking-widest disabled:opacity-40"
          >
            {busy ? "queueing..." : "run reindex"}
          </button>
          {msg && (
            <span className={`font-mono text-[11px] ${msg.ok ? "text-[var(--color-phosphor)]" : "text-[var(--color-amber)]"}`}>
              {msg.text}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function StatTile({ label, value, loading, accent, mono }: { label: string; value: any; loading?: boolean; accent?: boolean; mono?: boolean }) {
  return (
    <div className="panel rounded-[2px] p-3">
      <div className="label-xs">{label}</div>
      <div className={`mt-1 font-mono tabular-nums truncate ${accent ? "text-[var(--color-phosphor)] text-xl" : "text-[var(--color-text)] text-lg"} ${mono ? "text-sm" : ""}`}>
        {loading ? <span className="text-[var(--color-dim)]">···</span> : (value ?? <span className="text-[var(--color-dim)]">—</span>)}
      </div>
    </div>
  );
}
