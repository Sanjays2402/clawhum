from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class Segment:
    start_s: float
    end_s: float
    samples: np.ndarray


def segment_fixed(x: np.ndarray, sr: int, window_s: float = 6.0, hop_s: float = 3.0) -> list[Segment]:
    if len(x) == 0:
        return []
    win = int(window_s * sr)
    hop = int(hop_s * sr)
    if len(x) <= win:
        pad = np.zeros(win - len(x), dtype=x.dtype)
        return [Segment(0.0, len(x) / sr, np.concatenate([x, pad]))]
    out: list[Segment] = []
    i = 0
    idx = 0
    while i + win <= len(x):
        seg = x[i : i + win]
        out.append(Segment(i / sr, (i + win) / sr, seg))
        i += hop
        idx += 1
    return out


def segment_query(x: np.ndarray, sr: int, max_seconds: float = 10.0) -> Segment:
    n = int(max_seconds * sr)
    if len(x) > n:
        x = x[:n]
    return Segment(0.0, len(x) / sr, x)
