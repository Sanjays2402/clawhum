from __future__ import annotations
import numpy as np


def energy_vad(x: np.ndarray, sr: int, frame_ms: int = 30, thresh_db: float = -45.0) -> np.ndarray:
    frame = max(1, int(sr * frame_ms / 1000))
    if len(x) < frame:
        return np.ones(1, dtype=bool)
    n_frames = len(x) // frame
    energy = np.array([
        20.0 * np.log10(np.sqrt(np.mean(x[i*frame:(i+1)*frame] ** 2)) + 1e-9)
        for i in range(n_frames)
    ])
    return energy > thresh_db


def trim_silence(x: np.ndarray, sr: int, frame_ms: int = 30, thresh_db: float = -45.0) -> np.ndarray:
    voiced = energy_vad(x, sr, frame_ms, thresh_db)
    if not voiced.any():
        return x
    frame = int(sr * frame_ms / 1000)
    first = int(np.argmax(voiced))
    last = len(voiced) - int(np.argmax(voiced[::-1]))
    return x[first * frame : last * frame]
