from fastapi.testclient import TestClient

from clawhum_core.types import Track
from clawhum_embed.fallback import HashEmbedder
from clawhum_index.base import IndexedItem
from clawhum_index.numpy_index import NumpyIndex
from clawhum_index.persistence import write_metadata
from tests.fixtures.synth import melody


def _boot(monkeypatch, tmp_path, tracks):
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
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.app import create_app

    return create_app()


def _tracks():
    return [
        Track(id="local:alpha", title="Alpha Sonata", artist="Bach", album="Brandenburg", duration_s=120.0, source="local", tempo_bpm=92.0),
        Track(id="local:beta", title="Beta Waltz", artist="Strauss", album="Vienna", duration_s=180.5, source="local", tempo_bpm=72.0),
        Track(id="spotify:gamma", title="Gamma Pop", artist="Bach", album="Singles", duration_s=210.0, source="spotify", tempo_bpm=128.0),
    ]


def test_tracks_list_paginates_and_filters(monkeypatch, tmp_path):
    app = _boot(monkeypatch, tmp_path, _tracks())
    with TestClient(app) as c:
        # full list, default sort=title asc
        r = c.get("/tracks")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3
        assert [it["id"] for it in body["items"]] == ["local:alpha", "local:beta", "spotify:gamma"]
        assert body["items"][0]["title"] == "Alpha Sonata"
        assert body["items"][0]["has_audio"] is False  # no real path

        # substring search across artist
        r = c.get("/tracks", params={"q": "bach"})
        assert r.status_code == 200
        ids = sorted(it["id"] for it in r.json()["items"])
        assert ids == ["local:alpha", "spotify:gamma"]

        # source filter
        r = c.get("/tracks", params={"source": "spotify"})
        assert r.status_code == 200
        assert [it["id"] for it in r.json()["items"]] == ["spotify:gamma"]

        # sort by duration desc
        r = c.get("/tracks", params={"sort": "duration", "order": "desc"})
        assert r.status_code == 200
        assert [it["id"] for it in r.json()["items"]] == ["spotify:gamma", "local:beta", "local:alpha"]

        # pagination
        r = c.get("/tracks", params={"limit": 1, "offset": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["limit"] == 1
        assert body["offset"] == 1
        assert body["total"] == 3
        assert len(body["items"]) == 1


def test_track_detail_and_404(monkeypatch, tmp_path):
    app = _boot(monkeypatch, tmp_path, _tracks())
    with TestClient(app) as c:
        r = c.get("/track/local:alpha")
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "Alpha Sonata"
        assert body["artist"] == "Bach"
        assert body["source"] == "local"
        assert body["tempo_bpm"] == 92.0

        r = c.get("/track/does-not-exist")
        assert r.status_code == 404


def test_tracks_v1_alias(monkeypatch, tmp_path):
    app = _boot(monkeypatch, tmp_path, _tracks())
    with TestClient(app) as c:
        r = c.get("/v1/tracks", params={"q": "waltz"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "local:beta"
