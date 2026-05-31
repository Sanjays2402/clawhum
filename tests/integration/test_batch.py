"""Batch endpoint integration tests.

These exercise the real wiring: a zip is built in memory, posted to
``/batch``, and we assert the response shape for both JSON and CSV
output as well as the error-row behavior for an undecodable entry.
"""
from __future__ import annotations

import csv
import io
import zipfile

import soundfile as sf
from clawhum_embed.fallback import HashEmbedder
from clawhum_index.base import IndexedItem
from clawhum_index.numpy_index import NumpyIndex
from clawhum_index.persistence import write_metadata
from clawhum_core.types import Track
from fastapi.testclient import TestClient
from tests.fixtures.synth import melody


def _wav_bytes(x, sr=48000):
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV")
    return buf.getvalue()


def _seed_index(tmp_path, monkeypatch):
    e = HashEmbedder(dim=512)
    idx = NumpyIndex(dim=512)
    tracks = []
    for tid, freqs in [
        ("a", [440, 494, 523, 587]),
        ("b", [220, 247, 262, 294]),
    ]:
        v = e.embed(melody(freqs), 48000)
        idx.add([IndexedItem(track_id=tid, segment_index=0, vector=v, meta={})])
        tracks.append(Track(id=tid, title=tid.upper()))
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
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_THRESHOLD", "0.0")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()


def _zip_of(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in entries.items():
            zf.writestr(name, blob)
    return buf.getvalue()


def test_batch_json_and_error_row(tmp_path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from clawhum_api.app import create_app

    good = _wav_bytes(melody([440, 494, 523, 587]))
    payload = _zip_of({
        "clips/one.wav": good,
        "clips/two.wav": _wav_bytes(melody([220, 247, 262, 294])),
        "clips/broken.wav": b"this is not audio",
        # Ignored entries: hidden + macOS metadata + non-audio.
        "__MACOSX/clips/_one.wav": b"junk",
        "notes.txt": b"hello",
    })

    with TestClient(create_app()) as c:
        r = c.post(
            "/batch",
            files={"archive": ("hums.zip", payload, "application/zip")},
            data={"top_k": "3", "format": "json"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 3, body
        assert body["ok"] == 2
        assert body["failed"] == 1
        by_name = {row["filename"]: row for row in body["results"]}
        assert by_name["clips/one.wav"]["matches"][0]["track_id"] == "a"
        assert by_name["clips/two.wav"]["matches"][0]["track_id"] == "b"
        assert by_name["clips/broken.wav"]["error"]
        assert by_name["clips/broken.wav"]["matches"] == []


def test_batch_csv_download(tmp_path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from clawhum_api.app import create_app

    payload = _zip_of({"clips/one.wav": _wav_bytes(melody([440, 494, 523, 587]))})
    with TestClient(create_app()) as c:
        r = c.post(
            "/batch",
            files={"archive": ("hums.zip", payload, "application/zip")},
            data={"format": "csv", "top_k": "2"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert r.headers["X-Batch-Count"] == "1"
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        assert rows, "CSV body should not be empty"
        assert rows[0]["filename"] == "clips/one.wav"
        assert rows[0]["track_id"] == "a"


def test_batch_rejects_non_zip(tmp_path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from clawhum_api.app import create_app

    with TestClient(create_app()) as c:
        r = c.post(
            "/batch",
            files={"archive": ("bad.zip", b"not a zip", "application/zip")},
        )
        assert r.status_code == 400
        assert "zip" in r.json()["detail"].lower()


def test_batch_rejects_zip_with_no_audio(tmp_path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from clawhum_api.app import create_app

    payload = _zip_of({"notes.txt": b"hello", "readme.md": b"# hi"})
    with TestClient(create_app()) as c:
        r = c.post(
            "/batch",
            files={"archive": ("empty.zip", payload, "application/zip")},
        )
        assert r.status_code == 400
        assert "no audio" in r.json()["detail"].lower()
