"""Tests for the workspace IP allowlist.

Covers three guarantees that procurement reviews check:

1. Without rules, behaviour is unchanged (opt-in).
2. With rules, requests from a non-matching IP are rejected 403.
3. Rules from tenant A are invisible to tenant B and do not affect
   tenant B's traffic (no cross-tenant leakage at the query layer).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ipa.jsonl"))
    monkeypatch.setenv("CLAWHUM_TRUSTED_PROXIES_PATH", str(tmp_path / "tp.jsonl"))
    # Treat the loopback TestClient peer as a trusted proxy so the
    # X-Forwarded-For values these tests send are honoured. Without
    # this, the trusted proxies guard correctly rejects the spoofed
    # header from a non-proxy caller (covered in
    # test_trusted_proxies.py).
    monkeypatch.setenv("CLAWHUM_TRUSTED_PROXIES_GLOBAL", "127.0.0.0/8")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import ip_allowlist
    ip_allowlist.reset_cache()
    from clawhum_api import trusted_proxies
    trusted_proxies.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app(), client=("127.0.0.1", 50000))


def test_no_rules_means_no_restriction(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.get("/me", headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 200


def test_non_matching_ip_is_rejected(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    # Add a rule that only allows 10.0.0.0/8.
    r = c.post(
        "/ip-allowlist",
        json={"cidr": "10.0.0.0/8", "label": "office vpn"},
        headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "10.1.2.3"},
    )
    assert r.status_code == 201, r.text

    # Same allowed IP still works.
    ok = c.get("/me", headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "10.5.5.5"})
    assert ok.status_code == 200

    # Outside the allowlist gets 403 with a clear message.
    blocked = c.get("/me", headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "8.8.8.8"})
    assert blocked.status_code == 403
    assert "allowlist" in blocked.json()["detail"]


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    # Two tenants, both admin. Rules are scoped per tenant.
    spec = "acme_ops:sk_acme:9999:admin:acme,globex_ops:sk_globex:9999:admin:globex"
    c = _client(monkeypatch, tmp_path, spec)

    # Acme locks itself down to 10.0.0.0/8.
    r = c.post(
        "/ip-allowlist",
        json={"cidr": "10.0.0.0/8"},
        headers={"X-API-Key": "sk_acme", "X-Forwarded-For": "10.0.0.1"},
    )
    assert r.status_code == 201
    acme_rule_id = r.json()["id"]

    # Acme cannot see globex rules (none) and globex cannot see acme rules.
    g_list = c.get("/ip-allowlist", headers={"X-API-Key": "sk_globex", "X-Forwarded-For": "1.2.3.4"})
    assert g_list.status_code == 200
    assert g_list.json()["rules"] == []
    assert g_list.json()["enforcing"] is False

    # Globex traffic from any IP is still allowed because globex has no rules,
    # proving acme's rules did not leak into globex enforcement.
    g_me = c.get("/me", headers={"X-API-Key": "sk_globex", "X-Forwarded-For": "8.8.8.8"})
    assert g_me.status_code == 200

    # Globex cannot delete an acme rule even though it knows the id.
    bad = c.delete(
        f"/ip-allowlist/{acme_rule_id}",
        headers={"X-API-Key": "sk_globex", "X-Forwarded-For": "1.2.3.4"},
    )
    assert bad.status_code == 404

    # Acme can delete its own rule and enforcement turns off again.
    ok = c.delete(
        f"/ip-allowlist/{acme_rule_id}",
        headers={"X-API-Key": "sk_acme", "X-Forwarded-For": "10.0.0.1"},
    )
    assert ok.status_code == 204
    after = c.get("/me", headers={"X-API-Key": "sk_acme", "X-Forwarded-For": "8.8.8.8"})
    assert after.status_code == 200


def test_non_admin_cannot_manage_rules(monkeypatch, tmp_path):
    spec = "writer:sk_writer:9999:writer:acme,admin:sk_admin:9999:admin:acme"
    c = _client(monkeypatch, tmp_path, spec)
    forbidden = c.get(
        "/ip-allowlist",
        headers={"X-API-Key": "sk_writer", "X-Forwarded-For": "1.1.1.1"},
    )
    assert forbidden.status_code == 403
    write = c.post(
        "/ip-allowlist",
        json={"cidr": "10.0.0.0/24"},
        headers={"X-API-Key": "sk_writer", "X-Forwarded-For": "1.1.1.1"},
    )
    assert write.status_code == 403


def test_invalid_cidr_returns_400(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.post(
        "/ip-allowlist",
        json={"cidr": "not-a-cidr"},
        headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "1.1.1.1"},
    )
    assert r.status_code == 400
