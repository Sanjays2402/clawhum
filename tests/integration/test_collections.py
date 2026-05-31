from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str = "main:changeme:9999:writer"):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_COLLECTIONS_PATH", str(tmp_path / "collections.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _body(title: str = "My Picks", n: int = 2) -> dict:
    items = []
    for i in range(n):
        items.append(
            {
                "label": f"item {i}",
                "results": [
                    {
                        "track_id": f"t{i}",
                        "title": f"Track {i}",
                        "artist": "Tester",
                        "score": 0.5 + 0.1 * i,
                        "segment_index": 0,
                    }
                ],
                "query_id": f"q-{i}",
                "elapsed_ms": 30 + i,
                "filename": f"hum{i}.wav",
                "duration_sec": 2.5,
            }
        )
    return {"title": title, "note": "hello", "items": items}


def test_create_list_read_public(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # auth required for create
        assert c.post("/collections", json=_body()).status_code == 401

        r = c.post("/collections", json=_body(), headers={"X-API-Key": "changeme"})
        assert r.status_code == 200, r.text
        body = r.json()
        cid = body["id"]
        assert cid and len(cid) == 12
        assert body["url_path"] == f"/c/{cid}"

        # list (auth)
        r2 = c.get("/collections", headers={"X-API-Key": "changeme"})
        assert r2.status_code == 200, r2.text
        lst = r2.json()
        assert lst["total"] == 1
        assert lst["collections"][0]["id"] == cid
        assert lst["collections"][0]["item_count"] == 2
        assert lst["collections"][0]["title"] == "My Picks"

        # public read
        r3 = c.get(f"/collections/{cid}")
        assert r3.status_code == 200, r3.text
        got = r3.json()
        assert got["id"] == cid
        assert len(got["items"]) == 2
        assert got["items"][0]["results"][0]["title"] == "Track 0"
        assert got["note"] == "hello"


def test_validation_errors(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        # empty title
        bad = _body()
        bad["title"] = "   "
        assert c.post("/collections", json=bad, headers=h).status_code == 400

        # too many items
        too_many = _body()
        too_many["items"] = _body(n=2)["items"] * 30  # 60 > 50
        assert c.post("/collections", json=too_many, headers=h).status_code == 400


def test_update_and_delete(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        r = c.post("/collections", json=_body(title="v1"), headers=h)
        cid = r.json()["id"]

        # patch
        new_body = _body(title="v2", n=3)
        r2 = c.patch(f"/collections/{cid}", json=new_body, headers=h)
        assert r2.status_code == 200, r2.text
        assert r2.json()["title"] == "v2"
        assert r2.json()["item_count"] == 3

        # public sees the updated version
        r3 = c.get(f"/collections/{cid}")
        assert r3.json()["title"] == "v2"
        assert len(r3.json()["items"]) == 3

        # delete
        r4 = c.delete(f"/collections/{cid}", headers=h)
        assert r4.status_code == 200
        # public 404
        assert c.get(f"/collections/{cid}").status_code == 404
        # missing in list
        assert c.get("/collections", headers=h).json()["total"] == 0


def test_tenant_isolation(monkeypatch, tmp_path):
    keys = "alpha:key-a:9999:writer,beta:key-b:9999:writer"
    with _client(monkeypatch, tmp_path, api_keys=keys) as c:
        r = c.post("/collections", json=_body(), headers={"X-API-Key": "key-a"})
        cid = r.json()["id"]

        # beta cannot see alpha's collection in their list
        beta = c.get("/collections", headers={"X-API-Key": "key-b"}).json()
        assert beta["total"] == 0

        # beta cannot delete alpha's collection (treated as 404)
        assert (
            c.delete(f"/collections/{cid}", headers={"X-API-Key": "key-b"}).status_code
            == 404
        )

        # but the public read still works
        assert c.get(f"/collections/{cid}").status_code == 200


def test_missing_or_bad_id_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/collections/doesnotexist1").status_code == 404
        assert c.get("/collections/bad..id").status_code == 404


def test_v1_mount(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        r = c.post("/v1/collections", json=_body(), headers=h)
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        assert c.get(f"/v1/collections/{cid}").status_code == 200
