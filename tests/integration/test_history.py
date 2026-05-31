from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_API_KEYS", "main:changeme:10000:writer,other:othersecret:10000:writer")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _body(name="my hum", tags=None):
    return {
        "query_id": "q-1",
        "elapsed_ms": 17,
        "count": 2,
        "results": [
            {"track_id": "t1", "title": "Bohemian Rhapsody", "artist": "Queen", "score": 0.91},
            {"track_id": "t2", "title": "Somebody to Love", "artist": "Queen", "score": 0.62},
        ],
        "filename": "rec.wav",
        "duration_sec": 5.0,
        "name": name,
        "tags": tags or ["practice"],
    }


def test_history_create_list_patch_delete(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # auth required
        assert c.post("/history", json=_body()).status_code == 401

        h = {"X-API-Key": "changeme"}
        r = c.post("/history", json=_body(), headers=h)
        assert r.status_code == 200, r.text
        hid = r.json()["id"]
        assert len(hid) == 12

        # list
        lr = c.get("/history", headers=h)
        assert lr.status_code == 200
        lj = lr.json()
        assert lj["total"] == 1
        assert lj["items"][0]["id"] == hid
        assert lj["items"][0]["name"] == "my hum"
        assert lj["items"][0]["tags"] == ["practice"]

        # search by artist
        s = c.get("/history?q=queen", headers=h).json()
        assert s["total"] == 1
        s2 = c.get("/history?q=nope", headers=h).json()
        assert s2["total"] == 0

        # rename + retag
        pr = c.patch(f"/history/{hid}", json={"name": "renamed", "tags": ["Jazz", "jazz", "demo"]}, headers=h)
        assert pr.status_code == 200
        pj = pr.json()
        assert pj["name"] == "renamed"
        assert pj["tags"] == ["jazz", "demo"]

        # tag filter
        tf = c.get("/history?tag=jazz", headers=h).json()
        assert tf["total"] == 1

        # tenant isolation
        other = c.get("/history", headers={"X-API-Key": "othersecret"}).json()
        assert other["total"] == 0

        # get one
        g = c.get(f"/history/{hid}", headers=h)
        assert g.status_code == 200

        # delete
        d = c.delete(f"/history/{hid}", headers=h)
        assert d.status_code == 200
        assert c.get(f"/history/{hid}", headers=h).status_code == 404
        assert c.get("/history", headers=h).json()["total"] == 0


def test_history_validates(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        bad = _body()
        bad["results"] = []
        assert c.post("/history", json=bad, headers=h).status_code == 400
        assert c.get("/history/bad..id", headers=h).status_code == 404
