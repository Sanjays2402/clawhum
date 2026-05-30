from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, audit_path: Path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(audit_path))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_audit_skips_reads(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with _client(monkeypatch, tmp_path, audit_path) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 200
        assert c.get("/metrics").status_code == 200
    assert _read_events(audit_path) == []


def test_audit_records_mutations(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with _client(monkeypatch, tmp_path, audit_path) as c:
        # An unknown POST should still be audited even though it 404s, since
        # the audit log must capture attempted mutations for forensics.
        r = c.post("/feedback", json={}, headers={"x-api-key": "secret-token"})
        assert r.status_code in {200, 201, 400, 401, 404, 422}

    events = _read_events(audit_path)
    assert len(events) >= 1
    ev = events[-1]
    assert ev["method"] == "POST"
    assert ev["path"] == "/feedback"
    assert ev["status"] == r.status_code
    assert ev["actor"].startswith("key:")
    # Actor is a hashed digest, never the raw key.
    assert "secret-token" not in json.dumps(ev)
    assert ev["request_id"]
    assert ev["duration_ms"] >= 0


def test_audit_actor_anonymous_when_no_key(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with _client(monkeypatch, tmp_path, audit_path) as c:
        c.post("/feedback", json={})
    events = _read_events(audit_path)
    assert events, "expected at least one audit event"
    assert events[-1]["actor"] == "anonymous"
