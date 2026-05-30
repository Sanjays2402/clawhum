"""Tests for GDPR data lifecycle endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, audit_path: Path) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(audit_path))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_privacy_export_returns_only_caller_events(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with _client(monkeypatch, tmp_path, audit_path) as c:
        # Generate events for two different actors.
        c.post("/feedback", json={}, headers={"x-api-key": "alice-key"})
        c.post("/feedback", json={}, headers={"x-api-key": "bob-key"})
        c.post("/feedback", json={}, headers={"x-api-key": "alice-key"})

        r = c.get("/v1/privacy/export", headers={"x-api-key": "alice-key"})

    assert r.status_code == 200
    body = r.json()
    assert body["actor"].startswith("key:")
    # The export GET also produces no audit row because GETs are skipped,
    # so the count must reflect only alice's two POSTs.
    assert body["audit_event_count"] == 2
    assert all(ev["actor"] == body["actor"] for ev in body["audit_events"])
    # No raw key should ever leak into the export payload.
    assert "alice-key" not in r.text
    assert "bob-key" not in r.text


def test_privacy_delete_redacts_only_caller_rows(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with _client(monkeypatch, tmp_path, audit_path) as c:
        c.post("/feedback", json={}, headers={"x-api-key": "alice-key"})
        c.post("/feedback", json={}, headers={"x-api-key": "bob-key"})
        c.post("/feedback", json={}, headers={"x-api-key": "alice-key"})

        r = c.delete("/v1/privacy/me", headers={"x-api-key": "alice-key"})

    assert r.status_code == 200
    body = r.json()
    assert body["redacted_events"] == 2
    alice_actor = body["actor"]

    events = _read_lines(audit_path)
    # Audit log is append only. The original three POST rows are still
    # present plus one new row recording the DELETE itself, which is
    # written by the audit middleware after redact_actor returns.
    assert len(events) == 4
    redacted_rows = [ev for ev in events if ev.get("actor") == "redacted"]
    bob_rows = [ev for ev in events if ev.get("actor") != "redacted" and ev["method"] == "POST"]
    delete_rows = [ev for ev in events if ev["method"] == "DELETE"]
    assert len(redacted_rows) == 2
    assert len(bob_rows) == 1
    assert len(delete_rows) == 1
    # Bob's row is untouched and still attributable.
    assert bob_rows[0]["actor"].startswith("key:")
    assert bob_rows[0]["actor"] != alice_actor
    # Alice's POST rows have PII redacted but forensic fields remain.
    for row in redacted_rows:
        assert row["client_ip"] == "redacted"
        assert row["user_agent"] == "redacted"
        assert row["request_id"] == "redacted"
        assert row["method"] == "POST"
        assert row["path"] == "/feedback"
        assert "ts" in row and "status" in row
    # The DELETE row exists for forensic visibility. A follow up call to
    # the same endpoint would sweep it as well.
    assert delete_rows[0]["path"] == "/v1/privacy/me"


def test_privacy_export_empty_when_no_history(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with _client(monkeypatch, tmp_path, audit_path) as c:
        r = c.get("/v1/privacy/export", headers={"x-api-key": "fresh-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["audit_event_count"] == 0
    assert body["audit_events"] == []
