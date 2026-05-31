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


def test_history_export_csv_and_json(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        # seed two entries
        assert c.post("/history", json=_body(name="hum-1", tags=["jazz"]), headers=h).status_code == 200
        assert c.post("/history", json=_body(name="hum-2", tags=["rock"]), headers=h).status_code == 200

        # auth required
        assert c.get("/history/export").status_code == 401

        # csv
        r = c.get("/history/export?format=csv", headers=h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        body = r.text
        # header + 4 candidate rows (2 entries x 2 results each)
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert lines[0].startswith("history_id,created_at_iso,query_id")
        assert len(lines) == 1 + 4
        assert "Bohemian Rhapsody" in body
        assert "Queen" in body

        # json
        rj = c.get("/history/export?format=json", headers=h)
        assert rj.status_code == 200
        assert rj.headers["content-type"].startswith("application/json")
        payload = rj.json()
        assert payload["count"] == 2
        assert len(payload["items"]) == 2
        assert {it["name"] for it in payload["items"]} == {"hum-1", "hum-2"}

        # filter by tag narrows export
        rt = c.get("/history/export?format=json&tag=jazz", headers=h)
        assert rt.status_code == 200
        pt = rt.json()
        assert pt["count"] == 1
        assert pt["items"][0]["name"] == "hum-1"

        # invalid format rejected
        assert c.get("/history/export?format=xml", headers=h).status_code == 422

        # tenant isolation: other tenant sees nothing
        other = c.get("/history/export?format=json", headers={"X-API-Key": "othersecret"}).json()
        assert other["count"] == 0


def test_history_starred_and_sort(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}

        def mk(name, count, results):
            return {
                "query_id": f"q-{name}",
                "elapsed_ms": 10,
                "count": count,
                "results": results,
                "filename": f"{name}.wav",
                "duration_sec": 5.0,
                "name": name,
                "tags": [],
            }

        a = c.post("/history", json=mk("alpha", 1, [
            {"track_id": "t1", "title": "A", "artist": "X", "score": 0.50},
        ]), headers=h).json()["id"]
        b = c.post("/history", json=mk("bravo", 3, [
            {"track_id": "t2", "title": "B", "artist": "Y", "score": 0.99},
            {"track_id": "t3", "title": "B2", "artist": "Y", "score": 0.80},
            {"track_id": "t4", "title": "B3", "artist": "Y", "score": 0.70},
        ]), headers=h).json()["id"]
        d = c.post("/history", json=mk("charlie", 2, [
            {"track_id": "t5", "title": "C", "artist": "Z", "score": 0.30},
            {"track_id": "t6", "title": "C2", "artist": "Z", "score": 0.20},
        ]), headers=h).json()["id"]

        # default response includes starred=False
        items = c.get("/history", headers=h).json()["items"]
        assert all(it["starred"] is False for it in items)

        # star via PATCH
        pr = c.patch(f"/history/{b}", json={"starred": True}, headers=h)
        assert pr.status_code == 200, pr.text
        assert pr.json()["starred"] is True

        # starred filter
        sr = c.get("/history?starred=true", headers=h).json()
        assert sr["total"] == 1
        assert sr["items"][0]["id"] == b
        assert sr["items"][0]["starred"] is True

        # sort=results (count desc): bravo(3), charlie(2), alpha(1)
        rs = c.get("/history?sort=results", headers=h).json()
        assert [it["id"] for it in rs["items"]] == [b, d, a]

        # sort=name asc: alpha, bravo, charlie
        ns = c.get("/history?sort=name", headers=h).json()
        assert [it["name"] for it in ns["items"]] == ["alpha", "bravo", "charlie"]

        # sort=top_score desc: bravo(0.99), alpha(0.50), charlie(0.30)
        ts = c.get("/history?sort=top_score", headers=h).json()
        assert [it["id"] for it in ts["items"]] == [b, a, d]

        # invalid sort rejected
        assert c.get("/history?sort=bogus", headers=h).status_code == 422

        # unstar
        up = c.patch(f"/history/{b}", json={"starred": False}, headers=h)
        assert up.json()["starred"] is False
        assert c.get("/history?starred=true", headers=h).json()["total"] == 0
