"""Tests for ``GET /v1/webhooks/egress-ips``.

This endpoint discloses the source IP addresses that the deployment
uses when it dispatches outbound webhooks, so a customer's network
team can pin them in a corporate firewall instead of filing a support
ticket. The behaviours pinned here are the ones an enterprise buyer
will actually verify during procurement:

1. The endpoint requires authentication; an anonymous scanner cannot
   harvest the list.
2. When the operator has pinned ``CLAWHUM_WEBHOOK_EGRESS_IPS``, the
   response reports ``pinned=true`` and returns the parsed, deduped,
   validated address list (drops typos rather than 500ing).
3. When the operator has not pinned anything, ``pinned=false`` and the
   ``note`` field tells the caller exactly what to do, so the contract
   is unambiguous.
4. Every tenant sees the same disclosure (egress is deployment-wide
   infra, not tenant-scoped), but tenants without credentials still
   cannot reach it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, egress: str = ""):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "alpha:sk_alpha:9999:admin:acme,beta:sk_beta:9999:reader:beta",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_EGRESS_IPS", egress)
    monkeypatch.setenv("CLAWHUM_WEBHOOK_EGRESS_UPDATED_AT", "2026-05-30T18:00:00Z" if egress else "")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_egress_ips_requires_auth(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, egress="203.0.113.10")
    r = c.get("/v1/webhooks/egress-ips")
    assert r.status_code in (401, 403), r.text


def test_egress_ips_pinned_returns_parsed_and_deduped_list(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        # Mix of formats: spaces, commas, a CIDR, a duplicate, an IPv6, and a typo
        # that must be dropped silently rather than 500ing the endpoint.
        egress="203.0.113.10, 203.0.113.10 198.51.100.0/24\n2001:db8::/32 not-an-ip",
    )
    r = c.get("/v1/webhooks/egress-ips", headers={"X-API-Key": "sk_alpha"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pinned"] is True
    assert body["addresses"] == ["203.0.113.10", "198.51.100.0/24", "2001:db8::/32"]
    assert body["updated_at"] == "2026-05-30T18:00:00Z"
    assert "allowlist" in body["note"].lower()


def test_egress_ips_unpinned_says_so(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, egress="")
    r = c.get("/v1/webhooks/egress-ips", headers={"X-API-Key": "sk_alpha"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pinned"] is False
    assert body["addresses"] == []
    assert "not pinned" in body["note"].lower()


def test_egress_ips_visible_to_every_tenant(monkeypatch, tmp_path):
    """Egress is deployment infra, not tenant data, so a reader in a
    different workspace gets the same disclosure as an admin in another."""
    c = _client(monkeypatch, tmp_path, egress="203.0.113.10")
    r_admin = c.get("/v1/webhooks/egress-ips", headers={"X-API-Key": "sk_alpha"})
    r_reader = c.get("/v1/webhooks/egress-ips", headers={"X-API-Key": "sk_beta"})
    assert r_admin.status_code == 200
    assert r_reader.status_code == 200
    assert r_admin.json()["addresses"] == r_reader.json()["addresses"]
