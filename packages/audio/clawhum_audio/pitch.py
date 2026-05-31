"""Pitch contour extraction for query-by-humming explainability.

Produces a downsampled fundamental-frequency track (Hz + MIDI) that the
web UI overlays onto matched reference segments so a user can see *why*
two clips matched. Backed by librosa's pYIN implementation, which is
robust on monophonic hummed input.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import numpy as np


# Hum / whistle pitch range. Covers ~G2 (~98 Hz) to ~C6 (~1047 Hz).
_FMIN = 80.0
_FMAX = 1100.0


@dataclass
class PitchContour:
    sr: int
    duration_sec: float
    hop_sec: float
    times: list[float]
    hz: list[float | None]
    midi: list[float | None]
    voiced_ratio: float
    median_hz: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hz_to_midi(hz: np.ndarray) -> np.ndarray:
    out = np.full_like(hz, np.nan, dtype=np.float64)
    mask = np.isfinite(hz) & (hz > 0)
    out[mask] = 69.0 + 12.0 * np.log2(hz[mask] / 440.0)
    return out


def _downsample(x: np.ndarray, n: int) -> np.ndarray:
    """Decimating downsample preserving NaNs (unvoiced frames)."""
    if x.size <= n:
        return x
    idx = np.linspace(0, x.size - 1, n).round().astype(int)
    return x[idx]


def extract_pitch(
    x: np.ndarray,
    sr: int,
    *,
    start_sec: float | None = None,
    duration_sec: float | None = None,
    max_points: int = 240,
) -> PitchContour:
    """Extract a pitch contour from a (mono) audio array.

    Slices to ``[start_sec, start_sec + duration_sec)`` first when set,
    runs pYIN, then downsamples to at most ``max_points`` frames so the
    payload is cheap to ship to the browser.
    """
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = np.asarray(x, dtype=np.float32)

    if start_sec is not None or duration_sec is not None:
        s = int(max(0.0, start_sec or 0.0) * sr)
        e = x.size if duration_sec is None else min(x.size, s + int(duration_sec * sr))
        x = x[s:e]
    if x.size < int(0.1 * sr):
        # Too short for pYIN to be meaningful.
        return PitchContour(
            sr=sr, duration_sec=float(x.size) / sr, hop_sec=0.0,
            times=[], hz=[], midi=[], voiced_ratio=0.0, median_hz=0.0,
        )

    import librosa  # heavy import deferred

    hop_length = max(128, sr // 100)  # ~10 ms hop
    frame_length = max(1024, sr // 20)  # ~50 ms frame
    f0, voiced, _ = librosa.pyin(
        x, fmin=_FMIN, fmax=_FMAX, sr=sr,
        frame_length=frame_length, hop_length=hop_length,
        fill_na=np.nan,
    )
    f0 = np.asarray(f0, dtype=np.float64)
    voiced = np.asarray(voiced, dtype=bool)
    f0[~voiced] = np.nan

    f0_ds = _downsample(f0, max_points)
    midi_ds = _hz_to_midi(f0_ds)
    n = f0_ds.size
    hop_sec = float(x.size) / sr / max(1, n - 1) if n > 1 else 0.0
    times = (np.arange(n) * hop_sec).tolist()

    voiced_pts = f0_ds[np.isfinite(f0_ds)]
    voiced_ratio = float(voiced_pts.size) / float(max(1, n))
    median_hz = float(np.median(voiced_pts)) if voiced_pts.size else 0.0

    def _ser(arr: np.ndarray) -> list[float | None]:
        return [None if not np.isfinite(v) else round(float(v), 3) for v in arr]

    return PitchContour(
        sr=sr,
        duration_sec=float(x.size) / sr,
        hop_sec=hop_sec,
        times=[round(t, 4) for t in times],
        hz=_ser(f0_ds),
        midi=_ser(midi_ds),
        voiced_ratio=round(voiced_ratio, 4),
        median_hz=round(median_hz, 2),
    )
