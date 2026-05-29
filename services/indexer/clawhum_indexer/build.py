from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import io
import time

from clawhum_core.settings import get_settings
from clawhum_core.logging import get_logger
from clawhum_core.types import Track
from clawhum_audio.io import load_audio
from clawhum_audio.preprocess import to_mono, normalize
from clawhum_audio.segment import segment_fixed
from clawhum_audio.features import estimate_tempo, estimate_key
from clawhum_embed.factory import make_embedder
from clawhum_index.factory import make_index
from clawhum_index.base import IndexedItem
from clawhum_index.persistence import upsert_metadata
from clawhum_library.local import LocalLibrary

log = get_logger(__name__)


@dataclass
class IndexerOptions:
    library_path: Path | None = None
    spotify_playlist: str | None = None
    use_clap: bool = True
    segment_seconds: float = 6.0
    hop_seconds: float = 3.0
    max_segments_per_track: int = 8


def _process_track(t: Track, audio_bytes_or_path, embedder, opts: IndexerOptions, sr: int):
    audio, in_sr = load_audio(audio_bytes_or_path, target_sr=sr)
    audio = normalize(to_mono(audio))
    if not t.tempo_bpm:
        try: t.tempo_bpm = estimate_tempo(audio, sr)
        except Exception: pass
    if not t.key:
        try: t.key = estimate_key(audio, sr)
        except Exception: pass
    segs = segment_fixed(audio, sr, opts.segment_seconds, opts.hop_seconds)
    segs = segs[: opts.max_segments_per_track]
    if not segs:
        return []
    vecs = embedder.embed_batch([s.samples for s in segs], sr)
    items = []
    for i, (s, v) in enumerate(zip(segs, vecs)):
        items.append(IndexedItem(
            track_id=t.id, segment_index=i, vector=v,
            meta={"start_s": s.start_s, "end_s": s.end_s},
        ))
    return items


def build_index(opts: IndexerOptions | None = None) -> dict:
    s = get_settings()
    opts = opts or IndexerOptions()
    embedder = make_embedder(prefer_clap=opts.use_clap)
    index = make_index(dim=embedder.dim)
    # load existing
    index.load(str(s.index_path))

    sr = embedder.sr
    all_tracks: list[Track] = []
    n_added = 0
    t0 = time.perf_counter()

    if opts.library_path or s.library_path:
        lib = LocalLibrary(opts.library_path or s.library_path)
        for t in lib.tracks():
            try:
                items = _process_track(t, t.path, embedder, opts, sr)
                index.add(items)
                all_tracks.append(t)
                n_added += len(items)
                log.info("indexed_local", track=t.title, segments=len(items))
            except Exception as e:
                log.warning("index_local_failed", track=t.title, error=str(e))

    if opts.spotify_playlist:
        from clawhum_library.spotify import SpotifyLibrary
        sp = SpotifyLibrary()
        for t in sp.playlist_tracks(opts.spotify_playlist):
            if not t.preview_url:
                continue
            try:
                blob = sp.download_preview(t.preview_url)
                items = _process_track(t, io.BytesIO(blob), embedder, opts, sr)
                index.add(items)
                all_tracks.append(t)
                n_added += len(items)
                log.info("indexed_spotify", track=t.title, segments=len(items))
            except Exception as e:
                log.warning("index_spotify_failed", track=t.title, error=str(e))

    upsert_metadata(s.metadata_path, all_tracks)
    index.save(str(s.index_path))
    elapsed = time.perf_counter() - t0
    return {
        "tracks_added": len(all_tracks),
        "vectors_added": n_added,
        "index_size": index.size(),
        "elapsed_s": elapsed,
        "backend": index.__class__.__name__,
    }
