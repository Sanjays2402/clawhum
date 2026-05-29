from __future__ import annotations
from typing import Protocol
import numpy as np


class Embedder(Protocol):
    dim: int
    sr: int
    def embed(self, audio: np.ndarray, sr: int) -> np.ndarray: ...
    def embed_batch(self, audios: list[np.ndarray], sr: int) -> np.ndarray: ...
