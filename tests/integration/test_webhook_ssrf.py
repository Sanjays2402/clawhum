"""Outbound webhook SSRF protection.

These tests assert that destinations resolving to private, loopback,
link local, or cloud metadata addresses are rejected at create time
and that even if a previously valid URL starts resolving to a denied
address (DNS rebinding), the delivery worker refuses to send the
request and records a policy block in the delivery log.
"""

from __future__ import annotations

import ipaddress
import os

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "webhook_deliveries.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_ALLOWLIST_PATH", str(tmp_path / "webhook_allowlist.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        os.environ.get("_TEST_OVERRIDE_KEYS", "ops:adminkey:10000:admin"),
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_ENABLED", "false")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "true")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _patch_resolver(monkeypatch, mapping):
    """Replace DNS lookup with a static map: host -> list[str ip]."""
    from clawhum_api import webhook_safety

    def fake_resolve(host, port):
        ips = mapping.get(host)
        if ips is None:
            raise webhook_safety.WebhookDestinationError("dns lookup failed: test stub")
        return [ipaddress.ip_address(ip) for ip in ips]

    monkeypatch.setattr(webhook_safety, "_resolve", fake_resolve)


def test_imds_host_literal_rejected_at_create(monkeypatch, tmp_path):
    _patch_resolver(monkeypatch, {"169.254.169.254": ["169.254.169.254"]})
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "http://169.254.169.254/latest/meta-data/"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400
        assert "denied" in r.text.lower() or "blocked" in r.text.lower()


def test_loopback_resolution_rejected(monkeypatch, tmp_path):
    _patch_resolver(monkeypatch, {"sneaky.example.com": ["127.0.0.1"]})
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://sneaky.example.com/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400
        assert "loopback" in r.text.lower() or "private" in r.text.lower() \
            or "blocked" in r.text.lower()


def test_private_rfc1918_rejected(monkeypatch, tmp_path):
    _patch_resolver(monkeypatch, {"internal.example.com": ["10.0.0.5"]})
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://internal.example.com/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400


def test_public_address_accepted(monkeypatch, tmp_path):
    _patch_resolver(monkeypatch, {"api.example.com": ["93.184.216.34"]})
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://api.example.com/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text


def test_tenant_allowlist_unblocks_private_host(monkeypatch, tmp_path):
    _patch_resolver(monkeypatch, {"on-prem.acme.internal": ["10.20.30.40"]})
    with _client(monkeypatch, tmp_path) as c:
        # Without allowlist: blocked.
        r = c.post(
            "/webhooks",
            json={"url": "https://on-prem.acme.internal/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400

        # Admin adds the suffix to the workspace allowlist.
        r = c.put(
            "/webhooks/destination-allowlist",
            json={"hosts": ["acme.internal"]},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        assert "acme.internal" in r.json()["hosts"]

        # Now the same URL is accepted.
        r = c.post(
            "/webhooks",
            json={"url": "https://on-prem.acme.internal/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text


def test_allowlist_does_not_bypass_global_denylist(monkeypatch, tmp_path):
    _patch_resolver(monkeypatch, {"meta.evil": ["169.254.169.254"]})
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/webhooks/destination-allowlist",
            json={"hosts": ["evil"]},
            headers={"X-API-Key": "adminkey"},
        )
        r = c.post(
            "/webhooks",
            json={"url": "https://meta.evil/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400
        assert "denied" in r.text.lower()


def test_delivery_time_recheck_blocks_dns_rebind(monkeypatch, tmp_path):
    # At create time the host resolves to a public IP.
    _patch_resolver(monkeypatch, {"flips.example.com": ["93.184.216.34"]})
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://flips.example.com/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        hook_id = r.json()["id"]

        # Attacker repoints DNS at the metadata service.
        _patch_resolver(monkeypatch, {"flips.example.com": ["169.254.169.254"]})

        # Test fire should now be blocked at delivery time.
        r = c.post(
            f"/webhooks/{hook_id}/test",
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text

        # Delivery log records the policy block, not a network attempt.
        r = c.get(
            f"/webhooks/{hook_id}/deliveries",
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200
        deliveries = r.json()["deliveries"]
        assert deliveries, "expected at least one delivery record"
        latest = deliveries[0]
        assert latest["ok"] is False
        assert latest["status"] == 0
        assert "policy" in (latest.get("error") or "").lower() \
            or "blocked" in (latest.get("error") or "").lower()


def test_allowlist_admin_only(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "_TEST_OVERRIDE_KEYS",
        "ops:adminkey:10000:admin,ro:readkey:10000:reader",
    )
    _patch_resolver(monkeypatch, {})
    with _client(monkeypatch, tmp_path) as c:
        # GET works for any authed caller.
        r = c.get(
            "/webhooks/destination-allowlist",
            headers={"X-API-Key": "readkey"},
        )
        assert r.status_code == 200

        # PUT requires admin role.
        r = c.put(
            "/webhooks/destination-allowlist",
            json={"hosts": ["example.com"]},
            headers={"X-API-Key": "readkey"},
        )
        assert r.status_code == 403
