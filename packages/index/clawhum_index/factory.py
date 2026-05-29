from __future__ import annotations
from clawhum_core.settings import get_settings
from .base import VectorIndex
from .numpy_index import NumpyIndex


def make_index(dim: int | None = None) -> VectorIndex:
    s = get_settings()
    d = dim or s.embed_dim
    try:
        from .faiss_index import FaissHNSW
        return FaissHNSW(d)
    except Exception:
        return NumpyIndex(d)
