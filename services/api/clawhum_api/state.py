from __future__ import annotations
from dataclasses import dataclass
from clawhum_core.settings import get_settings
from clawhum_core.types import Track
from clawhum_embed.factory import make_embedder
from clawhum_index.factory import make_index
from clawhum_index.persistence import read_metadata


@dataclass
class AppState:
    embedder: object
    index: object
    tracks: dict[str, Track]

    @classmethod
    def boot(cls, prefer_clap: bool = True) -> "AppState":
        s = get_settings()
        emb = make_embedder(prefer_clap=prefer_clap)
        idx = make_index(dim=emb.dim)
        idx.load(str(s.index_path))
        tracks = {t.id: t for t in read_metadata(s.metadata_path)}
        return cls(embedder=emb, index=idx, tracks=tracks)
