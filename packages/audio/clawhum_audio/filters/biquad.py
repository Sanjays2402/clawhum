from __future__ import annotations
import numpy as np
from scipy.signal import butter, sosfilt


def _butter_sos(cutoff, sr, btype, order=4):
    nyq = sr * 0.5
    if isinstance(cutoff, (list, tuple)):
        wn = [c / nyq for c in cutoff]
    else:
        wn = cutoff / nyq
    return butter(order, wn, btype=btype, output="sos")


def high_pass(x: np.ndarray, sr: int, cutoff: float = 80.0, order: int = 4) -> np.ndarray:
    return sosfilt(_butter_sos(cutoff, sr, "highpass", order), x).astype("float32")


def low_pass(x: np.ndarray, sr: int, cutoff: float = 8000.0, order: int = 4) -> np.ndarray:
    return sosfilt(_butter_sos(cutoff, sr, "lowpass", order), x).astype("float32")


def band_pass(x: np.ndarray, sr: int, low: float = 80.0, high: float = 8000.0, order: int = 4) -> np.ndarray:
    return sosfilt(_butter_sos([low, high], sr, "bandpass", order), x).astype("float32")
