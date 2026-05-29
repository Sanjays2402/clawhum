from __future__ import annotations
import numpy as np


def estimate_tempo(x: np.ndarray, sr: int) -> float:
    try:
        import librosa
        tempo, _ = librosa.beat.beat_track(y=x, sr=sr)
        return float(tempo)
    except Exception:
        return 0.0


def estimate_key(x: np.ndarray, sr: int) -> str:
    try:
        import librosa
        chroma = librosa.feature.chroma_cqt(y=x, sr=sr)
        prof = chroma.mean(axis=1)
        names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
        return names[int(np.argmax(prof))]
    except Exception:
        return ""


def tempo_proximity(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    ratio = min(a, b) / max(a, b)
    # also reward half/double matches
    half = min(a, b * 2) / max(a, b * 2) if b > 0 else 0
    double = min(a, b / 2) / max(a, b / 2) if b > 0 else 0
    return max(ratio, half, double)
