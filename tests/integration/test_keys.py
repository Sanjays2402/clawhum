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


def test_pat_expiry_blocks_authentication(monkeypatch, tmp_path):
    """Expired PATs must not authenticate even though they are not revoked.

    Mints a token with a 1-day TTL, fast-forwards the clock past it by
    rewriting the on-disk record, and confirms the API rejects the
    bearer with 401 while still surfacing it as ``expired: true`` in
    the owner's list view.
    """
    import json as _json
    import time as _time

    with _client(monkeypatch, tmp_path) as c:
        # Mint a token with a short TTL.
        r = c.post(
            "/keys",
            json={"name": "shortlived", "expires_in_days": 1},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        secret = body["secret"]
        assert body["expires_at"] > _time.time()
        assert body["expired"] is False

        # Sanity check: it works right now.
        assert c.get("/me", headers={"X-API-Key": secret}).status_code == 200

        # Force the stored record to be in the past by appending a new
        # event for the same id with expires_at = 1 (epoch).
        pat_path = tmp_path / "pats.jsonl"
        latest: dict | None = None
        with pat_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = _json.loads(line)
                if rec.get("id") == body["id"]:
                    latest = rec
        assert latest is not None
        latest["expires_at"] = 1.0
        latest["last_used_at"] = float(latest.get("last_used_at", 0.0))
        with pat_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(latest) + "\n")

        # Expired token now fails auth with 401.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 401, r.text

        # Owner still sees the token in the list, marked expired.
        r = c.get("/keys", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["id"] == body["id"]
        assert rows[0]["expired"] is True

        # Policy endpoint reports the cap.
        r = c.get("/keys/policy", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        pol = r.json()
        assert pol["max_ttl_days"] >= 1
        assert pol["default_ttl_days"] >= 0


def test_pat_ttl_clamped_to_max(monkeypatch, tmp_path):
    """Asking for a longer TTL than the cap clamps to the cap."""
    monkeypatch.setenv("CLAWHUM_PAT_MAX_TTL_DAYS", "7")
    monkeypatch.setenv("CLAWHUM_PAT_DEFAULT_TTL_DAYS", "7")
    import time as _time

    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "ask-too-long", "expires_in_days": 365},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Should expire within ~7 days, not 365.
        ttl = body["expires_at"] - _time.time()
        assert 6 * 86400 < ttl < 8 * 86400, ttl

        # 0 ("never") is also clamped to the cap when a cap is set.
        r = c.post(
            "/keys",
            json={"name": "ask-never", "expires_in_days": 0},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200
        ttl2 = r.json()["expires_at"] - _time.time()
        assert 6 * 86400 < ttl2 < 8 * 86400, ttl2
