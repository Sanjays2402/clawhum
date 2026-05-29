from __future__ import annotations
import hashlib
import numpy as np


class HashEmbedder:
    """Deterministic spectral-hash embedder. Real features (MFCC + chroma)
    deterministically projected to a fixed dim. Used in CI and tests where
    CLAP weights are unavailable, but still produces meaningful similarity.
    """

    dim = 512
    sr = 48000

    def __init__(self, dim: int = 512, sr: int = 48000):
        self.dim = dim
        self.sr = sr

    def _features(self, x: np.ndarray, sr: int) -> np.ndarray:
        import librosa
        if sr != self.sr:
            from clawhum_audio.preprocess import resample
            x = resample(x, sr, self.sr)
            sr = self.sr
        if len(x) < sr // 2:
            x = np.pad(x, (0, sr // 2 - len(x)))
        mfcc = librosa.feature.mfcc(y=x, sr=sr, n_mfcc=40).mean(axis=1)
        chroma = librosa.feature.chroma_cqt(y=x, sr=sr).mean(axis=1)
        contrast = librosa.feature.spectral_contrast(y=x, sr=sr).mean(axis=1)
        feat = np.concatenate([mfcc, chroma, contrast]).astype(np.float32)
        # deterministic projection to self.dim
        rng = np.random.default_rng(seed=int(hashlib.sha256(b"clawhum-proj").hexdigest()[:8], 16))
        proj = rng.standard_normal((feat.shape[0], self.dim)).astype(np.float32)
        v = feat @ proj
        n = np.linalg.norm(v) + 1e-9
        return (v / n).astype(np.float32)

    def embed(self, audio: np.ndarray, sr: int) -> np.ndarray:
        return self._features(audio, sr)

    def embed_batch(self, audios: list[np.ndarray], sr: int) -> np.ndarray:
        return np.stack([self._features(a, sr) for a in audios], axis=0)
