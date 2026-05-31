"""Tests for /me identity endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str = "") -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    if api_keys:
        monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
        monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    else:
        monkeypatch.delenv("CLAWHUM_API_KEY", raising=False)
        monkeypatch.delenv("CLAWHUM_API_KEYS", raising=False)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "120")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_me_open_mode_reports_dev(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, api_keys="")
    r = client.get("/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth_mode"] == "open"
    assert body["tenant_id"] == "dev"
    assert body["key_name"] == "dev"
    assert "admin" in body["roles"]
    assert body["rate_limit_per_minute"] == 120
    assert body["masked_key"] == "dev"


def test_me_with_valid_key_returns_tenant(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        tmp_path,
        api_keys="acme:sk_live_acme_abcd1234:600:writer:acme",
    )
    r = client.get("/me", headers={"X-API-Key": "sk_live_acme_abcd1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth_mode"] == "key"
    assert body["tenant_id"] == "acme"
    assert body["key_name"] == "acme"
    assert body["roles"] == ["writer"]
    assert body["rate_limit_per_minute"] == 600
    assert body["masked_key"].endswith("1234")
    assert body["masked_key"].startswith("...")


def test_me_rejects_missing_key_in_key_mode(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        tmp_path,
        api_keys="ops:sk_live_ops_zzzz:60:admin:ops",
    )
    r = client.get("/me")
    assert r.status_code == 401
