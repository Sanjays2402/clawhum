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


def _write_wav(path, x, sr=48000):
    sf.write(str(path), x, sr, format="WAV")


def _boot(monkeypatch, tmp_path, library_root, tracks):
    e = HashEmbedder(dim=512)
    idx = NumpyIndex(dim=512)
    for t in tracks:
        v = e.embed(melody([440, 494, 523]), 48000)
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


def test_track_audio_streams_real_file(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    wav = lib / "song.wav"
    _write_wav(wav, melody([440, 494, 523]))
    track = Track(id="local:abc", title="Song", path=str(wav), source="local")
    app = _boot(monkeypatch, tmp_path, lib, [track])

    with TestClient(app) as c:
        r = c.get(f"/track/{track.id}/audio")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("audio/")
        assert len(r.content) > 100
        # Round-trip back through soundfile to prove it's a real wav.
        data, sr = sf.read(io.BytesIO(r.content), dtype="float32")
        assert sr == 48000
        assert data.shape[0] > 0


def test_track_audio_unknown_id_404s(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    app = _boot(monkeypatch, tmp_path, lib, [])
    with TestClient(app) as c:
        r = c.get("/track/local:does-not-exist/audio")
        assert r.status_code == 404


def test_track_audio_rejects_path_outside_library(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    outside = tmp_path / "outside.wav"
    _write_wav(outside, melody([440]))
    track = Track(id="local:outside", title="Outside", path=str(outside), source="local")
    app = _boot(monkeypatch, tmp_path, lib, [track])
    with TestClient(app) as c:
        r = c.get(f"/track/{track.id}/audio")
        assert r.status_code == 403
