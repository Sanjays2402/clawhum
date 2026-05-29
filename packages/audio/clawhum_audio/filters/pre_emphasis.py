from __future__ import annotations
import numpy as np


def pre_emphasis(x: np.ndarray, coef: float = 0.97) -> np.ndarray:
    if x.size == 0:
        return x
    return np.append(x[0], x[1:] - coef * x[:-1]).astype("float32")
