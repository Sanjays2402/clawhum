from __future__ import annotations
from typing import Optional
import numpy as np

try:
    import torch
    _TORCH = True
except Exception:
    _TORCH = False


def select_device(preference: str = "auto") -> str:
    if not _TORCH:
        return "cpu"
    p = (preference or "auto").lower()
    if p == "cpu":
        return "cpu"
    if p == "cuda" and torch.cuda.is_available():
        return "cuda"
    if p == "mps" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class ClapEmbedder:
    """Wraps HuggingFace CLAP (laion/clap-htsat-unfused). Model not bundled.

    Falls back gracefully if transformers/torch are missing; raises on .embed()
    so callers can switch to HashEmbedder for tests.
    """

    dim = 512
    sr = 48000

    def __init__(self, model_id: str = "laion/clap-htsat-unfused", device: str = "auto"):
        self.model_id = model_id
        self.device = select_device(device)
        self._model = None
        self._processor = None

    def _lazy_load(self) -> None:
        if self._model is not None:
            return
        if not _TORCH:
            raise RuntimeError("torch not installed; install clawhum[ml]")
        from transformers import ClapModel, ClapProcessor
        self._processor = ClapProcessor.from_pretrained(self.model_id)
        self._model = ClapModel.from_pretrained(self.model_id).to(self.device).eval()

    def embed(self, audio: np.ndarray, sr: int) -> np.ndarray:
        return self.embed_batch([audio], sr)[0]

    def embed_batch(self, audios: list[np.ndarray], sr: int) -> np.ndarray:
        self._lazy_load()
        import torch
        if sr != self.sr:
            from clawhum_audio.preprocess import resample
            audios = [resample(a, sr, self.sr) for a in audios]
        inputs = self._processor(audios=audios, sampling_rate=self.sr, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            feats = self._model.get_audio_features(**inputs)
        feats = torch.nn.functional.normalize(feats, dim=-1)
        return feats.detach().cpu().numpy().astype("float32")
