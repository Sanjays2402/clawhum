from __future__ import annotations
from collections import defaultdict
import time
import numpy as np

from clawhum_core.types import Match, Track
from clawhum_core.logging import get_logger
from clawhum_audio.preprocess import to_mono, normalize
from clawhum_audio.vad import trim_silence
from clawhum_audio.segment import segment_query
from clawhum_audio.features import estimate_tempo
from .rerank import tempo_rerank, threshold_filter

log = get_logger(__name__)


class Matcher:
    def __init__(self, embedder, index, tracks_by_id: dict[str, Track]):
        self.embedder = embedder
        self.index = index
        self.tracks = tracks_by_id

    def match(
        self,
        audio: np.ndarray,
        sr: int,
        top_k: int = 10,
        threshold: float = 0.0,
        rerank: bool = True,
        candidate_mult: int = 5,
    ) -> list[Match]:
        t0 = time.perf_counter()
        x = to_mono(audio)
        x = normalize(x)
        x = trim_silence(x, sr)
        seg = segment_query(x, sr, max_seconds=10.0)
        q = self.embedder.embed(seg.samples, sr)

        raw = self.index.search(q, k=top_k * candidate_mult)
        per_track: dict[str, tuple[float, int]] = {}
        for idx, score in raw:
            meta = self.index.meta(idx)
            tid = meta["track_id"]
            seg_idx = int(meta.get("segment_index", 0))
            cur = per_track.get(tid)
            if cur is None or score > cur[0]:
                per_track[tid] = (score, seg_idx)

        matches: list[Match] = []
        for tid, (score, seg_idx) in per_track.items():
            t = self.tracks.get(tid)
            if t is None:
                continue
            matches.append(Match(track=t, score=float(score), segment_index=seg_idx))

        matches = threshold_filter(matches, threshold)
        if rerank:
            try:
                q_tempo = estimate_tempo(seg.samples, sr)
                matches = tempo_rerank(matches, q_tempo)
            except Exception as e:
                log.warning("tempo_rerank_failed", error=str(e))

        matches.sort(key=lambda m: m.score, reverse=True)
        out = matches[:top_k]
        log.info("match_done", n_candidates=len(raw), n_tracks=len(per_track), top=len(out),
                 ms=int((time.perf_counter() - t0) * 1000))
        return out
