from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
import numpy as np


@dataclass
class IndexedItem:
    track_id: str
    segment_index: int
    vector: np.ndarray
    meta: dict


class VectorIndex(Protocol):
    dim: int
    def add(self, items: list[IndexedItem]) -> None: ...
    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]: ...
    def save(self, path: str) -> None: ...
    def load(self, path: str) -> None: ...
    def size(self) -> int: ...
    def meta(self, idx: int) -> dict: ...
