from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from .base import IndexedItem


class NumpyIndex:
    """Brute-force cosine index. Reliable fallback when FAISS unavailable."""

    def __init__(self, dim: int):
        self.dim = dim
        self._vecs: list[np.ndarray] = []
        self._meta: list[dict] = []

    def add(self, items: list[IndexedItem]) -> None:
        for it in items:
            v = it.vector.astype(np.float32)
            v = v / (np.linalg.norm(v) + 1e-9)
            self._vecs.append(v)
            self._meta.append({"track_id": it.track_id, "segment_index": it.segment_index, **it.meta})

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if not self._vecs:
            return []
        q = query.astype(np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        mat = np.stack(self._vecs)
        sims = mat @ q
        k = min(k, len(sims))
        top = np.argpartition(-sims, k - 1)[:k]
        order = top[np.argsort(-sims[top])]
        return [(int(i), float(sims[i])) for i in order]

    def _normalize(self, path: str) -> Path:
        p = Path(path)
        if p.suffix != ".npz":
            p = p.with_suffix(p.suffix + ".npz") if p.suffix else Path(str(p) + ".npz")
        return p

    def save(self, path: str) -> None:
        p = self._normalize(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez(str(p), vecs=np.stack(self._vecs) if self._vecs else np.zeros((0, self.dim), dtype=np.float32))
        with open(str(p) + ".meta.jsonl", "w") as f:
            for m in self._meta:
                f.write(json.dumps(m) + "\n")

    def load(self, path: str) -> None:
        p = self._normalize(path)
        if not p.exists():
            return
        z = np.load(str(p))
        vecs = z["vecs"]
        self._vecs = [vecs[i] for i in range(vecs.shape[0])]
        meta_path = str(p) + ".meta.jsonl"
        self._meta = []
        if Path(meta_path).exists():
            with open(meta_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._meta.append(json.loads(line))

    def size(self) -> int:
        return len(self._vecs)

    def meta(self, idx: int) -> dict:
        return self._meta[idx]
