"""Trusted reverse proxy enforcement.

Covers the guarantees a procurement security review will check:

1. With no trusted proxies configured, X-Forwarded-For is ignored and
   the socket peer is used. Spoofing the header cannot bypass the
   workspace IP allowlist.
2. With the loopback configured as a trusted proxy, the rightmost
   X-Forwarded-For entry is honoured (single hop ingress topology,
   the common case).
3. The workspace trusted proxy list is tenant scoped: tenant A's
   entries cannot be seen or modified by tenant B.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str, *, trusted_global: str = ""):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ipa.jsonl"))
    monkeypatch.setenv("CLAWHUM_TRUSTED_PROXIES_PATH", str(tmp_path / "tp.jsonl"))
    monkeypatch.setenv("CLAWHUM_TRUSTED_PROXIES_GLOBAL", trusted_global)
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


def test_spoofed_xff_cannot_bypass_workspace_allowlist(monkeypatch, tmp_path):
    """The headline guarantee.

    Tenant locks itself to 10.0.0.0/8. Direct caller (peer 127.0.0.1)
    sends a spoofed ``X-Forwarded-For: 10.5.5.5``. Without trusted
    proxies configured, the API must ignore the header, see the real
    peer (127.0.0.1), and reject. The pre-fix behaviour silently
    granted access.
    """
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme", trusted_global="")

    r = c.post(
        "/ip-allowlist",
        json={"cidr": "10.0.0.0/8", "label": "office"},
        headers={"X-API-Key": "sk_admin"},
    )
    assert r.status_code == 201, r.text

    spoofed = c.get(
        "/me",
        headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "10.5.5.5"},
    )
    assert spoofed.status_code == 403
    assert "127.0.0.1" in spoofed.json()["detail"]


def test_trusted_proxy_honours_xff_first_hop(monkeypatch, tmp_path):
    """Single-hop ingress topology works end to end."""
    c = _client(
        monkeypatch, tmp_path,
        "ops:sk_admin:9999:admin:acme",
        trusted_global="127.0.0.0/8",
    )
    r = c.post(
        "/ip-allowlist",
        json={"cidr": "10.0.0.0/8"},
        headers={"X-API-Key": "sk_admin"},
    )
    assert r.status_code == 201

    ok = c.get(
        "/me",
        headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "10.5.5.5"},
    )
    assert ok.status_code == 200

    bad = c.get(
        "/me",
        headers={"X-API-Key": "sk_admin", "X-Forwarded-For": "8.8.8.8"},
    )
    assert bad.status_code == 403
    assert "8.8.8.8" in bad.json()["detail"]


def test_trusted_proxy_list_is_tenant_scoped(monkeypatch, tmp_path):
    """Tenant A's workspace proxy list is invisible to tenant B."""
    spec = "acme_ops:sk_acme:9999:admin:acme,globex_ops:sk_globex:9999:admin:globex"
    c = _client(monkeypatch, tmp_path, spec, trusted_global="127.0.0.0/8")

    r = c.post(
        "/trusted-proxies",
        json={"cidr": "203.0.113.0/24", "label": "acme vpn"},
        headers={"X-API-Key": "sk_acme"},
    )
    assert r.status_code == 201, r.text
    acme_rule_id = r.json()["id"]

    g_list = c.get("/trusted-proxies", headers={"X-API-Key": "sk_globex"})
    assert g_list.status_code == 200
    assert g_list.json()["workspace_rules"] == []
    assert "127.0.0.0/8" in g_list.json()["global_cidrs"]

    bad = c.delete(
        f"/trusted-proxies/{acme_rule_id}",
        headers={"X-API-Key": "sk_globex"},
    )
    assert bad.status_code == 404
