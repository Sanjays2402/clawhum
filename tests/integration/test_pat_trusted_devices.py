"""Per-PAT trusted-device approval enforcement.

These tests prove the strict device approval gate is real end to
end: turning the bit on rejects every device until the workspace
owner approves a fingerprint, approval is fingerprint-scoped (one
approved device does NOT silently approve another), and turning the
bit back off restores unrestricted access.

The TestClient uses ``127.0.0.1`` as the synthetic peer; we drive
alternate IPs via the trusted X-Forwarded-For hop the auth layer
already honours. User-Agent families are exercised through the
standard ``User-Agent`` header so the fingerprint changes when we
switch from a ``curl``-style caller to a ``Chrome``-style caller.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_PAT_TRUSTED_DEVICES_PATH",
        str(tmp_path / "trusted_devices.jsonl"),
    )
    import os as _os
    if not _os.environ.get("CLAWHUM_API_KEYS_TEST_OVERRIDE"):
        monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:admin:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    from clawhum_api import pat_trusted_devices

    reset_registry_cache()
    pat_trusted_devices.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_strict_mode_blocks_until_approval_then_allows(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Mint a PAT.
        r = c.post(
            "/keys",
            json={"name": "ci-bot"},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        pat = r.json()
        pat_id = pat["id"]
        secret = pat["secret"]
        assert pat["require_device_approval"] is False

        # Without strict mode, any device works.
        ok = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "203.0.113.10",
                "User-Agent": "curl/8.4.0",
            },
        )
        assert ok.status_code == 200, ok.text

        # Turn strict mode on.
        r = c.put(
            f"/keys/{pat_id}/device-approval",
            json={"required": True},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["require_device_approval"] is True
        # Turning the bit on must NOT auto-trust the device that was
        # already in use. SOC2-style explicit-approval semantics.
        assert r.json()["has_approved_device"] is False

        # First request after strict mode is rejected with 403 +
        # the device fingerprint surfaced in a header.
        blocked = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "203.0.113.10",
                "User-Agent": "curl/8.4.0",
            },
        )
        assert blocked.status_code == 403, blocked.text
        assert "device" in blocked.json()["detail"].lower()
        fp = blocked.headers.get("x-device-fingerprint")
        assert fp and len(fp) == 16

        # The blocked device is now visible in the pending queue.
        listing = c.get(
            f"/keys/{pat_id}/devices",
            headers={"X-API-Key": "opskey"},
        )
        assert listing.status_code == 200, listing.text
        devices = listing.json()["devices"]
        assert len(devices) == 1
        assert devices[0]["fingerprint"] == fp
        assert devices[0]["status"] == "pending"
        # The pending sighting carries the resolved client IP and the
        # truncated User-Agent so an admin can identify it without
        # consulting the audit log.
        assert devices[0]["last_ip"] == "203.0.113.10"
        assert "curl" in devices[0]["last_ua"].lower()

        # An unrelated device fingerprint is still rejected even
        # while another is approved. We approve the first device
        # and prove the second one stays blocked.
        approval = c.post(
            f"/keys/{pat_id}/devices/{fp}/approve",
            json={"label": "ci runner"},
            headers={"X-API-Key": "opskey"},
        )
        assert approval.status_code == 200, approval.text
        assert approval.json()["device"]["status"] == "approved"
        assert approval.json()["device"]["label"] == "ci runner"

        # Same device now succeeds.
        ok = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "203.0.113.10",
                "User-Agent": "curl/8.4.0",
            },
        )
        assert ok.status_code == 200, ok.text

        # Different network + different UA family produces a
        # different fingerprint and is still rejected.
        alt = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "198.51.100.7",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh) AppleWebKit/605 "
                    "Chrome/118.0 Safari/605"
                ),
            },
        )
        assert alt.status_code == 403, alt.text
        alt_fp = alt.headers.get("x-device-fingerprint")
        assert alt_fp and alt_fp != fp

        # Revoking the approved device puts it back behind the gate.
        rev = c.delete(
            f"/keys/{pat_id}/devices/{fp}",
            headers={"X-API-Key": "opskey"},
        )
        assert rev.status_code == 200, rev.text
        blocked_again = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "203.0.113.10",
                "User-Agent": "curl/8.4.0",
            },
        )
        assert blocked_again.status_code == 403, blocked_again.text

        # Turning strict mode off restores unrestricted use without
        # any further approvals.
        off = c.put(
            f"/keys/{pat_id}/device-approval",
            json={"required": False},
            headers={"X-API-Key": "opskey"},
        )
        assert off.status_code == 200, off.text
        assert off.json()["require_device_approval"] is False
        ok = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "198.51.100.7",
                "User-Agent": "wget/1.21.4",
            },
        )
        assert ok.status_code == 200, ok.text


def test_devices_endpoint_is_tenant_scoped(monkeypatch, tmp_path):
    """Devices for a PAT in tenant A are not visible from tenant B."""
    monkeypatch.setenv("CLAWHUM_API_KEYS_TEST_OVERRIDE", "1")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:opskey:10000:admin:acme,beta:betakey:10000:admin:beta",
    )
    with _client(monkeypatch, tmp_path) as c:
        # Mint a PAT in acme and trigger one pending device.
        r = c.post(
            "/keys",
            json={"name": "acme-pat"},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        acme_pat_id = r.json()["id"]
        acme_secret = r.json()["secret"]
        r = c.put(
            f"/keys/{acme_pat_id}/device-approval",
            json={"required": True},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        # Trigger pending record.
        blocked = c.get(
            "/me",
            headers={
                "X-API-Key": acme_secret,
                "X-Forwarded-For": "203.0.113.10",
                "User-Agent": "curl/8.4.0",
            },
        )
        assert blocked.status_code == 403

        # Tenant beta probing acme's pat id gets 404, not 403, so
        # ids cannot be enumerated across workspaces.
        cross = c.get(
            f"/keys/{acme_pat_id}/devices",
            headers={"X-API-Key": "betakey"},
        )
        assert cross.status_code == 404, cross.text
        # And cross tenant approval is also 404.
        cross_approve = c.post(
            f"/keys/{acme_pat_id}/devices/0123456789abcdef/approve",
            json={},
            headers={"X-API-Key": "betakey"},
        )
        assert cross_approve.status_code == 404, cross_approve.text
