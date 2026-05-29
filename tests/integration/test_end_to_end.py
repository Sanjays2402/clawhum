import io
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from clawhum_core.types import Track
from clawhum_index.numpy_index import NumpyIndex
from clawhum_index.base import IndexedItem
from clawhum_index.persistence import write_metadata
from clawhum_embed.fallback import HashEmbedder
from tests.fixtures.synth import melody


def _wav_bytes(x, sr=48000):
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV")
    return buf.getvalue()


def test_match_endpoint(monkeypatch, tmp_path):
    e = HashEmbedder(dim=512)
    idx = NumpyIndex(dim=512)
    tracks = []
    for tid, freqs in [("a", [440, 494, 523, 587]),
                       ("b", [220, 247, 262, 294]),
                       ("c", [660, 740, 784, 880])]:
        v = e.embed(melody(freqs), 48000)
        idx.add([IndexedItem(track_id=tid, segment_index=0, vector=v, meta={})])
        tracks.append(Track(id=tid, title=tid.upper()))
    ix_path = tmp_path / "ix"
    md_path = tmp_path / "meta.jsonl"
    idx.save(str(ix_path))
    write_metadata(md_path, tracks)

    # Force NumPy backend so the API loads the same artifact we wrote
    monkeypatch.setattr("clawhum_index.factory.make_index",
                        lambda dim=None: NumpyIndex(dim or 512))
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(ix_path))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(md_path))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_THRESHOLD", "0.0")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app

    with TestClient(create_app()) as c:
        r = c.get("/stats")
        assert r.status_code == 200
        assert r.json()["tracks"] == 3

        audio = _wav_bytes(melody([440, 494, 523, 587]))
        r = c.post("/match", files={"audio": ("hum.wav", audio, "audio/wav")},
                   data={"top_k": "3", "threshold": "0.0"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["count"] >= 1
        assert j["results"][0]["track_id"] == "a"
