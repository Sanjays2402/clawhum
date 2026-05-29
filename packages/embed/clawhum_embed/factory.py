from __future__ import annotations
from clawhum_core.settings import get_settings
from .base import Embedder
from .clap import ClapEmbedder
from .fallback import HashEmbedder


def make_embedder(prefer_clap: bool = True) -> Embedder:
    s = get_settings()
    if not prefer_clap:
        return HashEmbedder(dim=s.embed_dim, sr=s.target_sr)
    try:
        return ClapEmbedder(model_id=s.model_id, device=s.device)
    except Exception:
        return HashEmbedder(dim=s.embed_dim, sr=s.target_sr)
