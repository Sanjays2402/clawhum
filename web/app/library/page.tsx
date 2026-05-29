"use client";
import { useEffect, useState } from "react";

interface Stats { tracks: number; vectors: number; dim: number; backend: string; }

export default function LibraryPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [path, setPath] = useState("");
  const [playlist, setPlaylist] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function refresh() {
    const r = await fetch("/api/stats");
    if (r.ok) setStats(await r.json());
  }
  useEffect(() => { refresh(); }, []);

  async function reindex() {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch("/api/reindex", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library_path: path || null, spotify_playlist: playlist || null, use_clap: false }),
      });
      const j = await r.json();
      setMsg(JSON.stringify(j));
    } finally { setBusy(false); }
  }

  return (
    <div className="px-6 py-16 max-w-3xl mx-auto">
      <h1 className="text-3xl font-bold mb-8">Library</h1>
      {stats && (
        <div className="grid grid-cols-4 gap-4 mb-10">
          <Stat label="Tracks" value={stats.tracks} />
          <Stat label="Vectors" value={stats.vectors} />
          <Stat label="Dim" value={stats.dim} />
          <Stat label="Backend" value={stats.backend} />
        </div>
      )}
      <div className="bg-[var(--panel)] border border-[var(--line)] rounded-xl p-6 space-y-4">
        <h2 className="font-semibold">Reindex</h2>
        <input value={path} onChange={e => setPath(e.target.value)}
               placeholder="Local audio directory"
               className="w-full bg-black/40 border border-[var(--line)] rounded px-3 py-2" />
        <input value={playlist} onChange={e => setPlaylist(e.target.value)}
               placeholder="Spotify playlist id (optional)"
               className="w-full bg-black/40 border border-[var(--line)] rounded px-3 py-2" />
        <button onClick={reindex} disabled={busy}
                className="px-4 py-2 rounded bg-[var(--accent)] text-black font-medium disabled:opacity-50">
          {busy ? "Working..." : "Start"}
        </button>
        {msg && <pre className="text-xs text-[var(--muted)] overflow-auto">{msg}</pre>}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-[var(--panel)] border border-[var(--line)] rounded-xl p-4">
      <div className="text-xs uppercase text-[var(--muted)]">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}
