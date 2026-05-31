from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_VIEWS_PATH", str(tmp_path / "views.jsonl"))
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_API_KEYS", "main:changeme:10000:writer,other:othersecret:10000:writer")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_history_views_crud_and_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # auth required
        assert c.get("/history/views").status_code == 401

        h = {"X-API-Key": "changeme"}

        # empty list
        lr = c.get("/history/views", headers=h)
        assert lr.status_code == 200
        assert lr.json() == {"items": [], "total": 0}

        # create
        body = {"name": "Starred jazz", "filters": {"q": "blue", "tag": "jazz", "sort": "top_score", "starred": True}}
        cr = c.post("/history/views", json=body, headers=h)
        assert cr.status_code == 200, cr.text
        vid = cr.json()["id"]
        assert len(vid) == 12

        # duplicate name rejected (case-insensitive, whitespace collapsed)
        dup = c.post("/history/views", json={"name": "  starred   JAZZ  ", "filters": {}}, headers=h)
        assert dup.status_code == 409

        # list contains it with collapsed name
        lj = c.get("/history/views", headers=h).json()
        assert lj["total"] == 1
        item = lj["items"][0]
        assert item["id"] == vid
        assert item["name"] == "Starred jazz"
        assert item["filters"] == {"q": "blue", "tag": "jazz", "sort": "top_score", "starred": True}

        # patch: rename and change filters
        pr = c.patch(f"/history/views/{vid}", json={"name": "Top jazz", "filters": {"q": "", "tag": "jazz", "sort": "recent", "starred": False}}, headers=h)
        assert pr.status_code == 200
        pj = pr.json()
        assert pj["name"] == "Top jazz"
        assert pj["filters"]["sort"] == "recent"
        assert pj["filters"]["starred"] is False

        # bad sort rejected by pydantic
        bad = c.post("/history/views", json={"name": "weird", "filters": {"sort": "exploding"}}, headers=h)
        assert bad.status_code == 422

        # tenant isolation: other tenant sees nothing, cannot patch / delete
        other = {"X-API-Key": "othersecret"}
        assert c.get("/history/views", headers=other).json() == {"items": [], "total": 0}
        assert c.patch(f"/history/views/{vid}", json={"name": "hijack"}, headers=other).status_code == 404
        assert c.delete(f"/history/views/{vid}", headers=other).status_code == 404

        # delete
        dr = c.delete(f"/history/views/{vid}", headers=h)
        assert dr.status_code == 200
        assert dr.json() == {"deleted": True}
        assert c.get("/history/views", headers=h).json() == {"items": [], "total": 0}

        # delete again -> 404
        assert c.delete(f"/history/views/{vid}", headers=h).status_code == 404

        # invalid id shape
        assert c.delete("/history/views/!!!bad!!!", headers=h).status_code == 400
        assert c.patch("/history/views/!!!bad!!!", json={"name": "x"}, headers=h).status_code == 400


def test_history_views_mounted_under_v1(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        cr = c.post("/v1/history/views", json={"name": "v1 view", "filters": {}}, headers=h)
        assert cr.status_code == 200, cr.text
        vid = cr.json()["id"]
        lj = c.get("/v1/history/views", headers=h).json()
        assert lj["total"] == 1 and lj["items"][0]["id"] == vid
