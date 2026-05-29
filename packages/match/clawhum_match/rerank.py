from __future__ import annotations
from clawhum_core.types import Match
from clawhum_audio.features import tempo_proximity


def threshold_filter(matches: list[Match], threshold: float) -> list[Match]:
    if threshold <= 0:
        return matches
    return [m for m in matches if m.score >= threshold]


def tempo_rerank(matches: list[Match], query_tempo: float, weight: float = 0.15) -> list[Match]:
    if query_tempo <= 0:
        return matches
    out: list[Match] = []
    for m in matches:
        t = m.track.tempo_bpm or 0.0
        bonus = tempo_proximity(query_tempo, t) * weight
        out.append(Match(track=m.track, score=m.score + bonus, segment_index=m.segment_index, reranked=True))
    return out
