from __future__ import annotations
import json
from pathlib import Path
import numpy as np

from .base import IndexedItem

try:
    import faiss
    _FAISS = True
except Exception:
    _FAISS = False


class FaissHNSW:
    """FAISS HNSW (cosine via inner product on normalized vectors)."""

    def __init__(self, dim: int, M: int = 32, ef_construction: int = 200, ef_search: int = 64):
        if not _FAISS:
            raise RuntimeError("faiss not installed")
        self.dim = dim
        self.index = faiss.IndexHNSWFlat(dim, M, faiss.METRIC_INNER_PRODUCT)
        self.index.hnsw.efConstruction = ef_construction
        self.index.hnsw.efSearch = ef_search
        self._meta: list[dict] = []

    def add(self, items: list[IndexedItem]) -> None:
        if not items:
            return
        vecs = np.stack([
            i.vector / (np.linalg.norm(i.vector) + 1e-9) for i in items
        ]).astype(np.float32)
        self.index.add(vecs)
        for it in items:
            self._meta.append({"track_id": it.track_id, "segment_index": it.segment_index, **it.meta})

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.index.ntotal == 0:
            return []
        q = query.astype(np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        D, I = self.index.search(q.reshape(1, -1), min(k, self.index.ntotal))
        return [(int(i), float(d)) for i, d in zip(I[0], D[0]) if i >= 0]

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(p))
        with open(str(p) + ".meta.jsonl", "w") as f:
            for m in self._meta:
                f.write(json.dumps(m) + "\n")

    def load(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        self.index = faiss.read_index(str(p))
        meta_path = str(p) + ".meta.jsonl"
        self._meta = []
        if Path(meta_path).exists():
            with open(meta_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._meta.append(json.loads(line))

    def size(self) -> int:
        return int(self.index.ntotal)

    def meta(self, idx: int) -> dict:
        return self._meta[idx]
