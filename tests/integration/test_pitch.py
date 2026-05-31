import io

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from clawhum_core.types import Track
from clawhum_embed.fallback import HashEmbedder
from clawhum_index.base import IndexedItem
from clawhum_index.numpy_index import NumpyIndex
from clawhum_index.persistence import write_metadata
from tests.fixtures.synth import melody


def _write_wav(path, x, sr=22050):
    sf.write(str(path), x, sr, format="WAV")


def _sine(freq: float, sr: int = 22050, dur: float = 1.5) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    return (0.7 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _boot(monkeypatch, tmp_path, library_root, tracks):
    e = HashEmbedder(dim=512)
    idx = NumpyIndex(dim=512)
    for t in tracks:
        v = e.embed(melody([440, 494, 523]), 22050)
        idx.add([IndexedItem(track_id=t.id, segment_index=0, vector=v, meta={})])
    ix_path = tmp_path / "ix"
    md_path = tmp_path / "meta.jsonl"
    idx.save(str(ix_path))
    write_metadata(md_path, tracks)
    monkeypatch.setattr(
        "clawhum_index.factory.make_index",
        lambda dim=None: NumpyIndex(dim or 512),
    )
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(ix_path))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(md_path))
    monkeypatch.setenv("CLAWHUM_LIBRARY_PATH", str(library_root))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.app import create_app

    return create_app()


def test_pitch_upload_returns_real_contour(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    app = _boot(monkeypatch, tmp_path, lib, [])
    buf = io.BytesIO()
    sf.write(buf, _sine(440.0), 22050, format="WAV")
    buf.seek(0)
    with TestClient(app) as c:
        r = c.post(
            "/pitch",
            files={"audio": ("sine.wav", buf.getvalue(), "audio/wav")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # A 440 Hz sine should land within ~5 Hz of 440 and be ~fully voiced.
        assert abs(body["median_hz"] - 440.0) < 5.0
        assert body["voiced_ratio"] > 0.9
        assert len(body["midi"]) > 50
        # Every voiced point should map to ~MIDI 69 (A4).
        voiced = [m for m in body["midi"] if m is not None]
        assert len(voiced) > 0
        assert abs(sum(voiced) / len(voiced) - 69.0) < 0.5


def test_track_pitch_returns_segment_contour(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    wav = lib / "song.wav"
    # 3 s of pure A4 sine so the segment slice is meaningful.
    sf.write(str(wav), _sine(440.0, dur=3.0), 22050, format="WAV")
    track = Track(id="local:abc", title="A4 sine", path=str(wav), source="local")
    app = _boot(monkeypatch, tmp_path, lib, [track])
    with TestClient(app) as c:
        r = c.get(f"/track/{track.id}/pitch", params={"segment_index": 1, "window": 1.0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["track_id"] == track.id
        assert body["segment_index"] == 1
        assert abs(body["median_hz"] - 440.0) < 5.0
        assert body["duration_sec"] <= 1.05


def test_track_pitch_unknown_id_404s(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    app = _boot(monkeypatch, tmp_path, lib, [])
    with TestClient(app) as c:
        r = c.get("/track/local:nope/pitch")
        assert r.status_code == 404


def test_track_pitch_rejects_bad_window(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    wav = lib / "song.wav"
    sf.write(str(wav), _sine(440.0), 22050, format="WAV")
    track = Track(id="local:abc", title="A4", path=str(wav), source="local")
    app = _boot(monkeypatch, tmp_path, lib, [track])
    with TestClient(app) as c:
        r = c.get(f"/track/{track.id}/pitch", params={"window": 0})
        assert r.status_code == 400
