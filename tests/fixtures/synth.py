from __future__ import annotations
import numpy as np


def tone(freq: float, seconds: float = 2.0, sr: int = 48000, amp: float = 0.5) -> np.ndarray:
    t = np.linspace(0, seconds, int(seconds * sr), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def melody(freqs: list[float], note_s: float = 0.4, sr: int = 48000) -> np.ndarray:
    return np.concatenate([tone(f, note_s, sr) for f in freqs])
