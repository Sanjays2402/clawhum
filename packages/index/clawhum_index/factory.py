from __future__ import annotations
from pathlib import Path
from clawhum_core.settings import get_settings
from .base import VectorIndex
from .numpy_index import NumpyIndex


def make_index(dim: int | None = None) -> VectorIndex:
    s = get_settings()
    d = dim or s.embed_dim
    # If an existing index file looks like NumPy artifact, use NumpyIndex.
    p = Path(s.index_path)
    candidates = [p, p.with_suffix(p.suffix + ".npz") if p.suffix != ".npz" else p, Path(str(p) + ".npz")]
    if any(c.exists() and str(c).endswith(".npz") for c in candidates):
        return NumpyIndex(d)
    try:
        from .faiss_index import FaissHNSW
        return FaissHNSW(d)
    except Exception:
        return NumpyIndex(d)
