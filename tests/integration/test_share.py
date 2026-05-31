from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_SHARES_PATH", str(tmp_path / "shares.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "main:changeme:0:writer")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _sample_body():
    return {
        "query_id": "q-abc",
        "elapsed_ms": 42,
        "count": 2,
        "results": [
            {"track_id": "t1", "title": "Test Song", "artist": "Tester", "score": 0.91, "segment_index": 3},
            {"track_id": "t2", "title": "Other", "artist": "Other", "score": 0.55, "segment_index": 0},
        ],
        "filename": "hum.wav",
        "duration_sec": 4.2,
    }


def test_share_create_and_read_public(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # create requires auth
        r0 = c.post("/share", json=_sample_body())
        assert r0.status_code == 401

        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        assert r.status_code == 200, r.text
        body = r.json()
        share_id = body["id"]
        assert share_id and len(share_id) == 12
        assert body["url_path"] == f"/r/{share_id}"

        # read is public (no api key header)
        r2 = c.get(f"/share/{share_id}")
        assert r2.status_code == 200, r2.text
        got = r2.json()
        assert got["id"] == share_id
        assert got["query_id"] == "q-abc"
        assert got["count"] == 2
        assert got["results"][0]["title"] == "Test Song"
        assert got["filename"] == "hum.wav"


def test_share_validates_empty_results(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        bad = _sample_body()
        bad["results"] = []
        r = c.post("/share", json=bad, headers={"X-API-Key": "changeme"})
        assert r.status_code == 400


def test_share_missing_id_returns_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/share/notarealid12")
        assert r.status_code == 404
        r2 = c.get("/share/bad..id")
        assert r2.status_code == 404
