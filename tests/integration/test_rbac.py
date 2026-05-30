"""Tests for role-based access control on API key routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _stub_reindex(monkeypatch, app):
    import clawhum_api.routes.library as lib_mod

    class _Stub:
        @staticmethod
        def boot(prefer_clap: bool = False):  # noqa: ARG004
            return app.state.clawhum

    monkeypatch.setattr(lib_mod, "AppState", _Stub)
    monkeypatch.setattr(lib_mod, "build_index", lambda opts: {"ok": True})


def test_role_parsing(monkeypatch):
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "ops:sk_ops:600:admin,partner:sk_p:120:writer|reader,ro:sk_ro::reader,bad:sk_bad:120:bogus",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "100")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import build_registry

    reg = build_registry(default_rpm=100)
    ops = reg.lookup("sk_ops")
    partner = reg.lookup("sk_p")
    ro = reg.lookup("sk_ro")
    bad = reg.lookup("sk_bad")

    assert ops.roles == frozenset({"admin"})
    assert ops.has_any(frozenset({"writer"}))
    assert partner.roles == frozenset({"writer", "reader"})
    assert not partner.has_role("admin")
    assert ro.roles == frozenset({"reader"})
    assert bad.roles == frozenset()
    assert not bad.has_any(frozenset({"reader"}))


def test_reader_cannot_reindex(monkeypatch, tmp_path):
    api_keys = "ops:sk_ops:9999:admin,ro:sk_ro:9999:reader"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        _stub_reindex(monkeypatch, c.app)

        r = c.post("/reindex", json={}, headers={"X-API-Key": "sk_ro"})
        assert r.status_code == 403, r.text
        assert "writer" in r.text

        r = c.post("/reindex", json={}, headers={"X-API-Key": "sk_ops"})
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True


def test_writer_cannot_delete_privacy(monkeypatch, tmp_path):
    api_keys = "w:sk_w:9999:writer,ops:sk_ops:9999:admin"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r = c.delete("/v1/privacy/me", headers={"X-API-Key": "sk_w"})
        assert r.status_code == 403, r.text

        r = c.delete("/v1/privacy/me", headers={"X-API-Key": "sk_ops"})
        assert r.status_code == 200, r.text


def test_reader_can_read_stats_and_export(monkeypatch, tmp_path):
    api_keys = "ro:sk_ro:9999:reader"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r = c.get("/stats", headers={"X-API-Key": "sk_ro"})
        assert r.status_code == 200, r.text

        r = c.get("/v1/privacy/export", headers={"X-API-Key": "sk_ro"})
        assert r.status_code == 200, r.text


def test_missing_key_still_401(monkeypatch, tmp_path):
    api_keys = "ops:sk_ops:9999:admin"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r = c.post("/reindex", json={})
        assert r.status_code == 401


def test_audit_log_records_roles(monkeypatch, tmp_path):
    api_keys = "ops:sk_ops:9999:admin,ro:sk_ro:9999:reader"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        audit_path = tmp_path / "audit.jsonl"
        r = c.post("/reindex", json={}, headers={"X-API-Key": "sk_ro"})
        assert r.status_code == 403

        assert audit_path.exists(), "audit log not written"
        events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        reindex_events = [e for e in events if e["path"] == "/reindex"]
        assert reindex_events, events
        ev = reindex_events[-1]
        assert ev["api_key_name"] == "ro"
        assert ev["roles"] == ["reader"]
        assert ev["status"] == 403


def test_dev_mode_grants_all_roles(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, api_keys="") as c:
        _stub_reindex(monkeypatch, c.app)

        r = c.post("/reindex", json={})
        assert r.status_code == 200, r.text

        r = c.delete("/v1/privacy/me")
        assert r.status_code == 200, r.text


def test_unknown_role_in_dependency_raises():
    from clawhum_api.auth import require_roles

    try:
        require_roles("not_a_role")
    except ValueError as exc:
        assert "unknown role" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown role")
