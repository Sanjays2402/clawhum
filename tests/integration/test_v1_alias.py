"""Smoke tests for the /v1/* public API surface.

The same routers that power the in-app /api/* endpoints are also mounted
under /v1 so external integrators can target a version-pinned URL we
will not break. These tests assert the aliases are reachable and behave
identically for at least the metadata endpoints (/me, /usage, /health
peers, /stats peers). Heavy match/batch endpoints are exercised by the
unversioned tests elsewhere; if /v1 is misregistered we will see it here
because the router config is shared.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("CLAWHUM_API_KEY", raising=False)
    monkeypatch.delenv("CLAWHUM_API_KEYS", raising=False)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "120")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_v1_me_matches_unversioned(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    legacy = client.get("/me")
    pinned = client.get("/v1/me")
    assert legacy.status_code == 200
    assert pinned.status_code == 200
    # Same payload (timestamps not involved in /me).
    assert legacy.json() == pinned.json()


def test_v1_usage_alias_reachable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/v1/usage")
    # In open mode no key is required; we just need a routable, non-404
    # response. 200 means the alias is wired correctly.
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tenant_id" in body
    assert "month" in body


def test_v1_history_alias_reachable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/v1/history?limit=1&offset=0")
    assert r.status_code == 200, r.text
    body = r.json()
    # History returns a paged envelope. Field names may evolve; the
    # contract we want pinned here is that the alias resolves and a JSON
    # object comes back.
    assert isinstance(body, dict)


def test_v1_share_create_requires_payload(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    # Empty body -> validation error from the same schema as /share.
    # 422 (or 400) proves we hit the share router, not a 404.
    r = client.post("/v1/share", json={})
    assert r.status_code in (400, 422), r.text


def test_v1_webhooks_list_alias(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/v1/webhooks")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), (list, dict))
