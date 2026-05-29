from __future__ import annotations
from pathlib import Path
from typing import Union
import io
import numpy as np
import soundfile as sf


def load_audio(src: Union[str, Path, bytes, io.BytesIO], target_sr: int | None = None) -> tuple[np.ndarray, int]:
    if isinstance(src, (str, Path)):
        data, sr = sf.read(str(src), dtype="float32", always_2d=False)
    else:
        if isinstance(src, (bytes, bytearray)):
            src = io.BytesIO(src)
        data, sr = sf.read(src, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if target_sr and sr != target_sr:
        from .preprocess import resample
        data = resample(data, sr, target_sr)
        sr = target_sr
    return data.astype(np.float32, copy=False), sr


def save_audio(path: str | Path, data: np.ndarray, sr: int) -> None:
    sf.write(str(path), data, sr, subtype="PCM_16")
