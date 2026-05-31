from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:writer:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_pat_create_authenticate_revoke(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Unauth blocked.
        assert c.get("/keys").status_code == 401

        # List starts empty.
        r = c.get("/keys", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        assert r.json() == []

        # Create a PAT.
        r = c.post(
            "/keys",
            json={"name": "ci-bot"},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        secret = body["secret"]
        pat_id = body["id"]
        assert secret.startswith("pat_")
        assert body["name"] == "ci-bot"
        assert "writer" in body["roles"]
        assert body["secret_hint"] == secret[-4:]

        # List now shows the token but never the secret.
        r = c.get("/keys", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert "secret" not in rows[0]
        assert rows[0]["id"] == pat_id

        # The minted PAT can authenticate against a protected route.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text
        me = r.json()
        assert me["tenant_id"] == "acme"
        assert me["key_name"] == "pat:ci-bot"

        # /v1 alias also works.
        assert c.get("/v1/keys", headers={"X-API-Key": secret}).status_code == 200

        # Revoke the PAT.
        r = c.delete(f"/keys/{pat_id}", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Revoked PAT no longer authenticates.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 401

        # Revoked PAT gone from list.
        r = c.get("/keys", headers={"X-API-Key": "opskey"})
        assert r.json() == []

        # Double revoke yields 404.
        r = c.delete(f"/keys/{pat_id}", headers={"X-API-Key": "opskey"})
        assert r.status_code == 404


def test_pat_tenant_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:acmekey:10000:writer:acme,globex:globexkey:10000:writer:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    with TestClient(create_app()) as c:
        # acme mints one.
        r = c.post(
            "/keys",
            json={"name": "acme-pat"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        acme_id = r.json()["id"]

        # globex cannot see it.
        r = c.get("/keys", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json() == []

        # globex cannot revoke it.
        r = c.delete(f"/keys/{acme_id}", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 404

        # acme still sees their own.
        r = c.get("/keys", headers={"X-API-Key": "acmekey"})
        assert len(r.json()) == 1
