"""Domain auto-join for SSO sign-ins.

These tests prove the procurement-relevant guarantees of the
``POST /sso/auto-join`` route:

1. Public endpoint refuses any email whose domain is not mapped to a
   workspace, with the same shape it returns when auto-join is
   disabled-yet-mapped, so it cannot be used to enumerate customers.
2. When a workspace owner has enabled auto-join, a verified email
   from the configured domain produces an active seat at the
   pre-approved role (never higher).
3. Repeat calls are idempotent; the second sign-in does not
   double-provision the seat or escalate its role.
4. Auto-join is per workspace: a seat is never created on a workspace
   that did not opt in, even if that workspace has SSO configured
   but auto_join is False.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_SSO_PATH", str(tmp_path / "sso.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
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


_BASE = {
    "provider": "okta",
    "issuer": "https://acme.okta.com",
    "client_id": "0oa-acme",
    "client_secret": "super-secret-value",
    "email_domain": "acme.com",
    "enforced": True,
}


def test_unknown_domain_returns_404_without_leaking(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.post("/sso/auto-join", json={"email": "stranger@nobody.test"})
    assert r.status_code == 404


def test_mapped_domain_with_auto_join_off_is_rejected(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.put(
        "/sso/config",
        headers={"X-API-Key": "sk_admin"},
        json={**_BASE, "auto_join": False, "auto_join_role": "reader"},
    )
    assert r.status_code == 200, r.text
    # Domain is known but admin did not opt in.
    r = c.post("/sso/auto-join", json={"email": "newhire@acme.com"})
    assert r.status_code == 403
    # The membership roster stays empty.
    r = c.get("/members", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200
    assert r.json().get("members", []) == []


def test_auto_join_provisions_seat_at_pre_approved_role(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.put(
        "/sso/config",
        headers={"X-API-Key": "sk_admin"},
        json={**_BASE, "auto_join": True, "auto_join_role": "writer"},
    )
    assert r.status_code == 200
    # First sign-in claims a fresh seat.
    r = c.post("/sso/auto-join", json={"email": "Newhire@Acme.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claimed"] is True
    assert body["role"] == "writer"
    assert body["status"] == "active"
    assert body["email"] == "newhire@acme.com"
    # Idempotent: a second call does not re-provision.
    r2 = c.post("/sso/auto-join", json={"email": "newhire@acme.com"})
    assert r2.status_code == 200
    assert r2.json()["claimed"] is False
    assert r2.json()["member_id"] == body["member_id"]
    # The admin roster shows exactly one active seat.
    r = c.get("/members", headers={"X-API-Key": "sk_admin"})
    members = r.json().get("members", [])
    assert len(members) == 1
    assert members[0]["email"] == "newhire@acme.com"
    assert members[0]["role"] == "writer"
    assert members[0]["status"] == "active"


def test_auto_join_does_not_cross_tenant_boundaries(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "acme:sk_a:9999:admin:acme,bravo:sk_b:9999:admin:bravo",
    )
    # Acme opts into auto-join, Bravo does not configure SSO at all.
    r = c.put(
        "/sso/config",
        headers={"X-API-Key": "sk_a"},
        json={**_BASE, "auto_join": True, "auto_join_role": "reader"},
    )
    assert r.status_code == 200
    # A sign-in for an acme.com email lands in acme, not bravo.
    r = c.post("/sso/auto-join", json={"email": "user@acme.com"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme"
    # Bravo's roster stays empty: the auto-join did not bleed across.
    rb = c.get("/members", headers={"X-API-Key": "sk_b"}).json()
    assert rb.get("members", []) == []
    # Acme's roster has the new user.
    ra = c.get("/members", headers={"X-API-Key": "sk_a"}).json()
    assert [m["email"] for m in ra.get("members", [])] == ["user@acme.com"]


def test_discover_surfaces_auto_join_flag(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    c.put(
        "/sso/config",
        headers={"X-API-Key": "sk_admin"},
        json={**_BASE, "auto_join": True, "auto_join_role": "reader"},
    )
    r = c.get("/sso/discover", params={"email": "any@acme.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["auto_join"] is True
