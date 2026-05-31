"""Tests for workspace single sign on configuration.

Procurement checklist this exercises:

1. Discovery is public, returns nothing-interesting when unknown.
2. Admin-only routes reject anonymous and reader-role callers.
3. Cross tenant isolation: tenant A cannot read or overwrite tenant B's
   SSO config, and tenant B cannot claim tenant A's email domain.
4. Enforce flag round-trips and is surfaced on /me so the web UI can
   gate the password sign in screen without privileged access.
5. Client secret is masked when read back; mutations are validated.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_SSO_PATH", str(tmp_path / "sso.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_PATH", str(tmp_path / "mfa.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import sso_store
    sso_store.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


_VALID = {
    "provider": "okta",
    "issuer": "https://acme.okta.com",
    "client_id": "0oa-acme",
    "client_secret": "super-secret-value",
    "email_domain": "acme.com",
    "enforced": True,
}


def test_discover_returns_unconfigured_for_unknown_domain(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.get("/sso/discover", params={"email": "nobody@example.test"})
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["enforced"] is False
    assert body["issuer"] == ""


def test_providers_lists_known_idps(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.get("/sso/providers")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()["providers"]}
    assert {"okta", "azure", "google"}.issubset(ids)


def test_admin_can_upsert_and_read_masked_secret(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.put("/sso/config", headers={"X-API-Key": "sk_admin"}, json=_VALID)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["provider"] == "okta"
    assert rec["enforced"] is True
    assert rec["client_secret"].endswith("alue") and "super" not in rec["client_secret"]

    r = c.get("/sso/config", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200
    assert r.json()["issuer"] == "https://acme.okta.com"

    r = c.get("/sso/discover", params={"email": "person@acme.com"})
    body = r.json()
    assert body["configured"] is True and body["enforced"] is True
    assert body["provider"] == "okta"


def test_non_admin_cannot_write(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "ops:sk_admin:9999:admin:acme,reader:sk_reader:9999:reader:acme",
    )
    r = c.put("/sso/config", headers={"X-API-Key": "sk_reader"}, json=_VALID)
    assert r.status_code == 403
    r = c.put("/sso/config", json=_VALID)  # no key at all
    assert r.status_code == 401


def test_reader_cannot_read_config_either(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "ops:sk_admin:9999:admin:acme,reader:sk_reader:9999:reader:acme",
    )
    c.put("/sso/config", headers={"X-API-Key": "sk_admin"}, json=_VALID)
    r = c.get("/sso/config", headers={"X-API-Key": "sk_reader"})
    assert r.status_code == 403


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "a_admin:sk_a:9999:admin:acme,b_admin:sk_b:9999:admin:bravo",
    )
    # Acme writes its config.
    r = c.put("/sso/config", headers={"X-API-Key": "sk_a"}, json=_VALID)
    assert r.status_code == 200
    # Bravo reads /sso/config: must not see Acme's record.
    r = c.get("/sso/config", headers={"X-API-Key": "sk_b"})
    assert r.status_code == 200
    assert r.json() in (None, {})
    # Bravo cannot squat on acme.com as its email domain.
    r = c.put(
        "/sso/config",
        headers={"X-API-Key": "sk_b"},
        json={**_VALID, "issuer": "https://bravo.okta.com", "client_id": "0oa-bravo"},
    )
    assert r.status_code == 400
    assert "already claimed" in r.json()["detail"]
    # Bravo can take its own domain.
    r = c.put(
        "/sso/config",
        headers={"X-API-Key": "sk_b"},
        json={
            **_VALID,
            "issuer": "https://bravo.okta.com",
            "client_id": "0oa-bravo",
            "email_domain": "bravo.com",
        },
    )
    assert r.status_code == 200
    # Discovery must route by domain, not by caller.
    assert c.get("/sso/discover", params={"email": "x@acme.com"}).json()["issuer"] == \
        "https://acme.okta.com"
    assert c.get("/sso/discover", params={"email": "x@bravo.com"}).json()["issuer"] == \
        "https://bravo.okta.com"


def test_me_surfaces_sso_state(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    me = c.get("/me", headers={"X-API-Key": "sk_admin"}).json()
    assert me["sso_configured"] is False
    assert me["sso_enforced"] is False
    c.put("/sso/config", headers={"X-API-Key": "sk_admin"}, json=_VALID)
    me = c.get("/me", headers={"X-API-Key": "sk_admin"}).json()
    assert me["sso_configured"] is True
    assert me["sso_enforced"] is True
    assert me["sso_provider"] == "okta"
    assert me["sso_email_domain"] == "acme.com"


def test_delete_is_idempotent_and_admin_only(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "ops:sk_admin:9999:admin:acme,reader:sk_reader:9999:reader:acme",
    )
    c.put("/sso/config", headers={"X-API-Key": "sk_admin"}, json=_VALID)
    r = c.delete("/sso/config", headers={"X-API-Key": "sk_reader"})
    assert r.status_code == 403
    r = c.delete("/sso/config", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 204
    # Idempotent: deleting again is a no-op success.
    r = c.delete("/sso/config", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 204
    assert c.get("/sso/discover", params={"email": "x@acme.com"}).json()["configured"] is False


def test_validates_issuer_and_domain(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    bad = {**_VALID, "issuer": "ftp://not-a-real-issuer"}
    r = c.put("/sso/config", headers={"X-API-Key": "sk_admin"}, json=bad)
    assert r.status_code == 400
    bad = {**_VALID, "email_domain": "not a domain"}
    r = c.put("/sso/config", headers={"X-API-Key": "sk_admin"}, json=bad)
    assert r.status_code == 400


def test_v1_alias_is_mounted(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.get("/v1/sso/providers")
    assert r.status_code == 200
    assert any(p["id"] == "okta" for p in r.json()["providers"])
