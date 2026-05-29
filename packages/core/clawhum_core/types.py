from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass(slots=True)
class Track:
    id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_s: float = 0.0
    path: Optional[str] = None
    preview_url: Optional[str] = None
    artwork_url: Optional[str] = None
    source: str = "local"
    tempo_bpm: Optional[float] = None
    key: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "artist": self.artist,
            "album": self.album, "duration_s": self.duration_s, "path": self.path,
            "preview_url": self.preview_url, "artwork_url": self.artwork_url,
            "source": self.source, "tempo_bpm": self.tempo_bpm, "key": self.key,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(**d)


@dataclass(slots=True)
class Embedding:
    track_id: str
    segment_index: int
    vector: np.ndarray
    start_s: float = 0.0
    end_s: float = 0.0


@dataclass(slots=True)
class Match:
    track: Track
    score: float
    segment_index: int = 0
    reranked: bool = False


@dataclass(slots=True)
class IndexStats:
    track_count: int
    vector_count: int
    dim: int
    backend: str
