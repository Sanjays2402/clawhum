from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "main:changeme:10000:writer,other:othersecret:10000:writer",
    )
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _match_body(name: str = "morning hum"):
    return {
        "query_id": "q-1",
        "elapsed_ms": 17,
        "count": 1,
        "results": [
            {"track_id": "t1", "title": "Yellow", "artist": "Coldplay", "score": 0.81},
        ],
        "filename": "rec.wav",
        "duration_sec": 4.2,
        "name": name,
        "tags": ["test"],
    }


def test_activity_requires_auth(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/activity").status_code == 401


def test_activity_lists_matches_and_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h_main = {"X-API-Key": "changeme"}
        h_other = {"X-API-Key": "othersecret"}

        r = c.post("/history", json=_match_body("first"), headers=h_main)
        assert r.status_code == 200, r.text
        r = c.post("/history", json=_match_body("second"), headers=h_main)
        assert r.status_code == 200

        c.post("/history", json=_match_body("not yours"), headers=h_other)

        ar = c.get("/activity", headers=h_main)
        assert ar.status_code == 200, ar.text
        body = ar.json()
        assert body["total"] == 2
        kinds = {i["kind"] for i in body["items"]}
        assert kinds == {"match"}
        titles = [i["title"] for i in body["items"]]
        assert "first" in titles and "second" in titles
        # Other tenant must not bleed in.
        assert "not yours" not in titles
        # latest_at echoes the newest item's timestamp.
        assert body["latest_at"] == body["items"][0]["created_at"]

        # Kind filter narrows correctly even when no deliveries exist.
        only_deliveries = c.get("/activity?kind=delivery", headers=h_main).json()
        assert only_deliveries["total"] == 0
        assert only_deliveries["latest_at"] == 0.0

        # `since` strictly excludes anything at or before the cursor.
        cursor = body["latest_at"]
        empty = c.get(f"/activity?since={cursor}", headers=h_main).json()
        assert empty["total"] == 0


def test_activity_limit_caps(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "changeme"}
        for i in range(5):
            c.post("/history", json=_match_body(f"row {i}"), headers=h)
        r = c.get("/activity?limit=2", headers=h).json()
        assert len(r["items"]) == 2
        assert r["total"] == 5
        # 422 on out-of-range limit (FastAPI pydantic validation)
        assert c.get("/activity?limit=0", headers=h).status_code == 422
        assert c.get("/activity?limit=999", headers=h).status_code == 422
