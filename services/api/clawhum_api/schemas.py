from __future__ import annotations
from pydantic import BaseModel, Field


class MatchResult(BaseModel):
    track_id: str
    title: str
    artist: str = ""
    album: str = ""
    score: float
    segment_index: int = 0
    preview_url: str | None = None
    artwork_url: str | None = None
    source: str = "local"
    tempo_bpm: float | None = None


class MatchResponse(BaseModel):
    query_id: str
    elapsed_ms: int
    count: int
    results: list[MatchResult]


class FeedbackBody(BaseModel):
    query_id: str
    track_id: str
    score: float
    vote: int = Field(ge=-1, le=1)


class StatsResponse(BaseModel):
    tracks: int
    vectors: int
    dim: int
    backend: str


class ReindexBody(BaseModel):
    library_path: str | None = None
    spotify_playlist: str | None = None
    use_clap: bool = True


class HealthResponse(BaseModel):
    ok: bool = True
    version: str
    embedder: str
    index_backend: str
    tracks: int
    vectors: int
