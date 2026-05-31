"""GET /feedback returns the current tenant's votes with a summary."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.delenv("CLAWHUM_API_KEY", raising=False)
    monkeypatch.delenv("CLAWHUM_API_KEYS", raising=False)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_get_feedback_returns_rows_and_summary(monkeypatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)

    votes = [
        {"query_id": "q1", "track_id": "t-a", "score": 0.81, "vote": 1},
        {"query_id": "q1", "track_id": "t-b", "score": 0.42, "vote": -1},
        {"query_id": "q2", "track_id": "t-a", "score": 0.77, "vote": 1},
    ]
    for v in votes:
        r = client.post("/feedback", json=v)
        assert r.status_code == 200, r.text

    r = client.get("/feedback")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total"] == 3
    assert body["summary"]["confirm"] == 2
    assert body["summary"]["reject"] == 1
    assert body["summary"]["unique_queries"] == 2
    assert body["summary"]["unique_tracks"] == 2
    assert len(body["rows"]) == 3
    # newest first ordering
    ts_seq = [row["ts"] for row in body["rows"]]
    assert ts_seq == sorted(ts_seq, reverse=True)

    # filter by vote
    r = client.get("/feedback", params={"vote": 1})
    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert all(row["vote"] == 1 for row in r.json()["rows"])

    # filter by track_id
    r = client.get("/feedback", params={"track_id": "t-a"})
    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert all(row["track_id"] == "t-a" for row in r.json()["rows"])

    # pagination
    r = client.get("/feedback", params={"limit": 1, "offset": 1})
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 3
    assert len(j["rows"]) == 1
