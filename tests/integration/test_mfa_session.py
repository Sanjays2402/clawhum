"""MFA step-up ("sudo mode") session tokens.

After one successful TOTP challenge the dashboard can present the
issued ``X-MFA-Session`` token instead of typing a code for every
destructive call. The token must be HMAC-bound to (tenant, actor),
expire on time, and die when MFA is disabled or sessions are force
logged out.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, ttl="300"):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_PATH", str(tmp_path / "mfa.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ip_allowlist.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS",
                       "acme:opskey:10000:admin:acme,beta:betakey:10000:admin:beta")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_SESSION_TTL_SECONDS", ttl)
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _enroll(c, key):
    r = c.post("/mfa/enroll", headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    from clawhum_api.mfa import totp
    r = c.post("/mfa/verify", json={"code": totp(secret)},
               headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return secret


def _mint_pat(c, key, name="ci"):
    r = c.post("/keys", json={"name": name}, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_session_token_unlocks_destructive_routes(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        from clawhum_api.mfa import totp
        secret = _enroll(c, "opskey")
        r = c.post("/mfa/session", json={"code": totp(secret)},
                   headers={"X-API-Key": "opskey"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert r.json()["ttl_seconds"] == 300
        pat_id = _mint_pat(c, "opskey", "with-session")
        r = c.delete(f"/keys/{pat_id}",
                     headers={"X-API-Key": "opskey", "X-MFA-Session": token})
        assert r.status_code == 200, r.text


def test_session_token_rejected_cross_actor(monkeypatch, tmp_path):
    """A token minted by tenant A's admin must not work for tenant B."""
    with _client(monkeypatch, tmp_path) as c:
        from clawhum_api.mfa import totp
        secret_a = _enroll(c, "opskey")
        _enroll(c, "betakey")
        r = c.post("/mfa/session", json={"code": totp(secret_a)},
                   headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        token_a = r.json()["token"]
        pat_id = _mint_pat(c, "betakey", "x-tenant")
        r = c.delete(f"/keys/{pat_id}",
                     headers={"X-API-Key": "betakey", "X-MFA-Session": token_a})
        assert r.status_code == 401, r.text
        assert "mfa session invalid" in r.json()["detail"]


def test_session_token_dies_when_mfa_disabled(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        from clawhum_api.mfa import totp
        secret = _enroll(c, "opskey")
        r = c.post("/mfa/session", json={"code": totp(secret)},
                   headers={"X-API-Key": "opskey"})
        token = r.json()["token"]
        r = c.request("DELETE", "/mfa", json={"code": totp(secret)},
                      headers={"X-API-Key": "opskey"})
        assert r.status_code == 200, r.text
        _enroll(c, "opskey")
        pat_id = _mint_pat(c, "opskey", "post-disable")
        r = c.delete(f"/keys/{pat_id}",
                     headers={"X-API-Key": "opskey", "X-MFA-Session": token})
        assert r.status_code == 401, r.text


def test_session_disabled_when_ttl_zero(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, ttl="0") as c:
        from clawhum_api.mfa import totp
        secret = _enroll(c, "opskey")
        r = c.post("/mfa/session", json={"code": totp(secret)},
                   headers={"X-API-Key": "opskey"})
        assert r.status_code == 409, r.text
        r = c.get("/mfa/session", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        assert r.json()["enabled"] is False


def test_session_revoke_invalidates_outstanding_token(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        from clawhum_api.mfa import totp
        secret = _enroll(c, "opskey")
        r = c.post("/mfa/session", json={"code": totp(secret)},
                   headers={"X-API-Key": "opskey"})
        token = r.json()["token"]
        r = c.delete("/mfa/session", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        pat_id = _mint_pat(c, "opskey", "post-revoke")
        r = c.delete(f"/keys/{pat_id}",
                     headers={"X-API-Key": "opskey", "X-MFA-Session": token})
        assert r.status_code == 401, r.text
