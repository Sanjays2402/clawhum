"""Multi-tenant scoping: key registry, request state, feedback isolation."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_tenant_parsed_from_spec(monkeypatch):
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "ops:sk_ops:600:admin:Acme Co!,partner:sk_p:120:writer:globex,ro:sk_ro::reader",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "100")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import build_registry, reset_registry_cache
    reset_registry_cache()

    reg = build_registry(default_rpm=100)
    # Unsafe chars get stripped, lowercased.
    assert reg.lookup("sk_ops").tenant_id == "acmeco"
    assert reg.lookup("sk_p").tenant_id == "globex"
    # Omitted tenant id falls back to the key name.
    assert reg.lookup("sk_ro").tenant_id == "ro"


def test_feedback_is_scoped_per_tenant(monkeypatch, tmp_path):
    api_keys = (
        "acme_ops:sk_acme:9999:admin:acme,"
        "globex_ops:sk_globex:9999:admin:globex"
    )
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r1 = c.post(
            "/feedback",
            json={"query_id": "q1", "track_id": "t-acme", "score": 0.9, "vote": 1},
            headers={"X-API-Key": "sk_acme"},
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["tenant_id"] == "acme"

        r2 = c.post(
            "/feedback",
            json={"query_id": "q2", "track_id": "t-globex", "score": 0.7, "vote": -1},
            headers={"X-API-Key": "sk_globex"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["tenant_id"] == "globex"

        feedback_file = tmp_path / "feedback.jsonl"
        rows = [json.loads(line) for line in feedback_file.read_text().splitlines() if line.strip()]
        assert len(rows) == 2
        assert {r["tenant_id"] for r in rows} == {"acme", "globex"}

        # Acme export must only contain Acme's feedback rows.
        ex = c.get("/v1/privacy/export", headers={"X-API-Key": "sk_acme"})
        assert ex.status_code == 200, ex.text
        body = ex.json()
        assert body["tenant_id"] == "acme"
        assert body["feedback_row_count"] == 1
        assert body["feedback_rows"][0]["track_id"] == "t-acme"

        # Globex export must only contain Globex's feedback rows.
        ex2 = c.get("/v1/privacy/export", headers={"X-API-Key": "sk_globex"})
        assert ex2.json()["feedback_row_count"] == 1
        assert ex2.json()["feedback_rows"][0]["track_id"] == "t-globex"


def test_delete_my_data_redacts_only_caller_tenant(monkeypatch, tmp_path):
    api_keys = (
        "acme:sk_acme:9999:admin:acme,"
        "globex:sk_globex:9999:admin:globex"
    )
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        c.post("/feedback", json={"query_id": "qa", "track_id": "ta", "score": 1.0, "vote": 1},
               headers={"X-API-Key": "sk_acme"})
        c.post("/feedback", json={"query_id": "qg", "track_id": "tg", "score": 1.0, "vote": 1},
               headers={"X-API-Key": "sk_globex"})

        r = c.delete("/v1/privacy/me", headers={"X-API-Key": "sk_acme"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == "acme"
        assert body["redacted_feedback_rows"] == 1

        rows = [
            json.loads(line)
            for line in (tmp_path / "feedback.jsonl").read_text().splitlines()
            if line.strip()
        ]
        acme_rows = [r for r in rows if r["tenant_id"] == "acme"]
        globex_rows = [r for r in rows if r["tenant_id"] == "globex"]
        assert acme_rows[0]["track_id"] == "redacted"
        # Globex row is untouched.
        assert globex_rows[0]["track_id"] == "tg"


def test_audit_log_records_tenant_id(monkeypatch, tmp_path):
    api_keys = "acme:sk_acme:9999:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r = c.post(
            "/feedback",
            json={"query_id": "q", "track_id": "t", "score": 0.5, "vote": 0},
            headers={"X-API-Key": "sk_acme"},
        )
        assert r.status_code == 200
        assert r.headers.get("x-tenant-id") == "acme"

        audit_path = tmp_path / "audit.jsonl"
        events = [
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if line.strip()
        ]
        fb = [e for e in events if e["path"] == "/feedback"]
        assert fb, events
        assert fb[-1]["tenant_id"] == "acme"


def test_dev_mode_falls_back_to_dev_tenant(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, api_keys="") as c:
        r = c.post(
            "/feedback",
            json={"query_id": "q", "track_id": "t", "score": 0.5, "vote": 0},
        )
        assert r.status_code == 200, r.text
        assert r.json()["tenant_id"] == "dev"
        assert r.headers.get("x-tenant-id") == "dev"
