from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str = "main:changeme:9999:writer"):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_SHARES_PATH", str(tmp_path / "shares.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
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


def test_share_list_and_revoke(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # list requires auth
        assert c.get("/share").status_code == 401

        # empty list to start
        r0 = c.get("/share", headers={"X-API-Key": "changeme"})
        assert r0.status_code == 200, r0.text
        assert r0.json() == {"shares": [], "total": 0}

        # create two shares
        r1 = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        r2 = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        id1 = r1.json()["id"]
        id2 = r2.json()["id"]
        assert id1 != id2

        listed = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert listed["total"] == 2
        ids = {s["id"] for s in listed["shares"]}
        assert ids == {id1, id2}
        first = listed["shares"][0]
        assert first["url_path"] == f"/r/{first['id']}"
        assert first["top_title"] == "Test Song"
        assert first["top_artist"] == "Tester"
        assert first["count"] == 2

        # revoke needs auth
        assert c.delete(f"/share/{id1}").status_code == 401

        # revoke succeeds
        dr = c.delete(f"/share/{id1}", headers={"X-API-Key": "changeme"})
        assert dr.status_code == 200, dr.text
        assert dr.json() == {"ok": True, "id": id1}

        # public read now 404
        assert c.get(f"/share/{id1}").status_code == 404
        # list no longer contains it
        after = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert after["total"] == 1
        assert after["shares"][0]["id"] == id2

        # second revoke returns 404
        assert c.delete(f"/share/{id1}", headers={"X-API-Key": "changeme"}).status_code == 404


def test_share_revoke_is_tenant_scoped(monkeypatch, tmp_path):
    api_keys = "alice:secret-a:9999:writer:tenant-a,bob:secret-b:9999:writer:tenant-b"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "secret-a"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        # bob cannot see alice's share in his list
        bobs = c.get("/share", headers={"X-API-Key": "secret-b"}).json()
        assert bobs == {"shares": [], "total": 0}

        # bob cannot revoke alice's share
        deny = c.delete(f"/share/{sid}", headers={"X-API-Key": "secret-b"})
        assert deny.status_code == 404

        # but public read still works (it's a public link)
        assert c.get(f"/share/{sid}").status_code == 200

        # alice can revoke her own
        ok = c.delete(f"/share/{sid}", headers={"X-API-Key": "secret-a"})
        assert ok.status_code == 200
        assert c.get(f"/share/{sid}").status_code == 404
