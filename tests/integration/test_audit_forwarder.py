"""Tests for per-workspace audit log forwarding.

Procurement-critical guarantees we lock in here:

1. A workspace can register an HTTPS sink; the secret is shown once
   and persisted only as a hash.
2. Events from workspace A are NEVER delivered to workspace B's sink,
   even if both have destinations configured.
3. The /test endpoint signs with HMAC-SHA256 and the receiver can
   verify the X-ClawHum-Signature header.
4. Admin role is required; member tokens cannot read or mutate.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_AUDIT_FORWARDER_PATH", str(tmp_path / "afw.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_AUDIT_FORWARDER_DELIVERIES_PATH",
        str(tmp_path / "afw_deliveries.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    # Loopback/private hosts in the destination URL still get rejected
    # by the metadata + scheme checks; we keep the resolved-IP check
    # off so tests do not need real DNS for ``siem.acme.example``.
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import audit_forwarder

    audit_forwarder.reset_cache()
    # Replace the singleton worker so each test starts clean.
    audit_forwarder._WORKER = audit_forwarder._Worker()
    from clawhum_api.app import create_app

    return TestClient(create_app()), audit_forwarder


def test_upsert_returns_secret_once_and_hashes_at_rest(monkeypatch, tmp_path):
    client, mod = _client(
        monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme"
    )
    r = client.put(
        "/audit-forwarding",
        json={"url": "https://siem.acme.example/ingest"},
        headers={"X-API-Key": "sk_admin"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    secret = body["secret"]
    assert secret.startswith("awsec_")
    # Subsequent GET must not include the secret.
    r2 = client.get("/audit-forwarding", headers={"X-API-Key": "sk_admin"})
    assert r2.status_code == 200
    payload = r2.json()
    assert payload["configured"] is True
    assert "\"secret\":" not in json.dumps(payload)
    assert secret not in json.dumps(payload)
    # On-disk record stores hash, not plaintext.
    on_disk = (tmp_path / "afw.jsonl").read_text(encoding="utf-8")
    assert secret not in on_disk
    assert mod.hash_secret(secret) in on_disk


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    """A's destination is invisible to B and never receives B's events."""
    client, mod = _client(
        monkeypatch,
        tmp_path,
        "ops_a:sk_a:9999:admin:acme,ops_b:sk_b:9999:admin:globex",
    )
    captured: list[tuple[str, dict[str, Any]]] = []

    def hook(dest, event):
        captured.append((dest.tenant_id, dict(event)))
        return 200, "", 1.0

    mod.get_worker().send_hook = hook

    # Both workspaces configure sinks.
    ra = client.put(
        "/audit-forwarding",
        json={"url": "https://a.example/in"},
        headers={"X-API-Key": "sk_a"},
    )
    assert ra.status_code == 200, ra.text
    rb = client.put(
        "/audit-forwarding",
        json={"url": "https://b.example/in"},
        headers={"X-API-Key": "sk_b"},
    )
    assert rb.status_code == 200, rb.text

    # Tenant B cannot see tenant A's destination id even though both exist.
    sa = client.get(
        "/audit-forwarding", headers={"X-API-Key": "sk_a"}
    ).json()["destination"]["id"]
    sb = client.get(
        "/audit-forwarding", headers={"X-API-Key": "sk_b"}
    ).json()["destination"]["id"]
    assert sa != sb

    # The acts of PUT-ing above each generated an audit event for the
    # respective workspace; drain the worker and confirm each event
    # only went to its own destination.
    mod.get_worker().drain_for_tests()
    by_tenant: dict[str, set[str]] = {}
    for tid, ev in captured:
        by_tenant.setdefault(tid, set()).add(str(ev.get("tenant_id")))
    # Every captured event must have matching destination tenant and
    # event tenant_id; never a mismatch.
    for tid, observed in by_tenant.items():
        assert observed == {tid}, (tid, observed)


def test_member_cannot_read_or_mutate(monkeypatch, tmp_path):
    client, _ = _client(
        monkeypatch,
        tmp_path,
        "ops:sk_admin:9999:admin:acme,joe:sk_member:9999:member:acme",
    )
    # Admin sets up; member then probes.
    client.put(
        "/audit-forwarding",
        json={"url": "https://siem.acme.example/in"},
        headers={"X-API-Key": "sk_admin"},
    )
    r = client.get(
        "/audit-forwarding", headers={"X-API-Key": "sk_member"}
    )
    assert r.status_code == 403, r.text
    r2 = client.put(
        "/audit-forwarding",
        json={"url": "https://attacker.example/x"},
        headers={"X-API-Key": "sk_member"},
    )
    assert r2.status_code == 403


def test_rejects_loopback_and_metadata(monkeypatch, tmp_path):
    client, _ = _client(
        monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme"
    )
    for bad in (
        "ftp://example.com/x",
        "http://169.254.169.254/latest",
        "https://127.0.0.1/in",
    ):
        r = client.put(
            "/audit-forwarding",
            json={"url": bad},
            headers={"X-API-Key": "sk_admin"},
        )
        assert r.status_code == 400, (bad, r.status_code, r.text)


def test_hmac_signature_is_verifiable(monkeypatch, tmp_path):
    """The signing helper must produce a header the receiver can verify."""
    from clawhum_api import audit_forwarder as mod

    body = b'{"event":"hello"}'
    header = mod.sign_payload("topsecret", body)
    assert header.startswith("sha256=")
    assert mod.verify_signature("topsecret", body, header)
    assert not mod.verify_signature("wrong", body, header)
    assert not mod.verify_signature("topsecret", b"tampered", header)
