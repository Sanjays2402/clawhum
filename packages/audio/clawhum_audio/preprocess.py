from __future__ import annotations
import numpy as np


def to_mono(x: np.ndarray) -> np.ndarray:
    if x.ndim == 1:
        return x
    return x.mean(axis=-1).astype(np.float32, copy=False)


def normalize(x: np.ndarray, peak: float = 0.99) -> np.ndarray:
    m = float(np.max(np.abs(x))) if x.size else 0.0
    if m < 1e-9:
        return x
    return (x * (peak / m)).astype(np.float32, copy=False)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    try:
        import librosa
        return librosa.resample(x.astype(np.float32), orig_sr=sr_in, target_sr=sr_out)
    except Exception:
        # poly fallback
        from math import gcd
        g = gcd(sr_in, sr_out)
        up, down = sr_out // g, sr_in // g
        from scipy.signal import resample_poly
        return resample_poly(x, up, down).astype(np.float32, copy=False)
