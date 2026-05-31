"""Per-PAT IP allowlist enforcement.

These tests prove the per-credential IP fence is real: a token minted
with an allowlist is usable from a matching client IP and rejected
from any other, the workspace-wide allowlist remains independent,
malformed CIDRs return a structured 400, and clearing the list
restores unrestricted access.

The TestClient uses ``127.0.0.1`` as the synthetic peer; we drive
alternate IPs via the trusted X-Forwarded-For hop the auth layer
already honours.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:writer:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_pat_ip_allowlist_blocks_off_range_and_allows_in_range(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Mint a PAT pinned to 203.0.113.0/24.
        r = c.post(
            "/keys",
            json={"name": "ci-bot", "ip_cidrs": ["203.0.113.0/24"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ip_cidrs"] == ["203.0.113.0/24"]
        secret = body["secret"]

        # Off-range IP is rejected with 403.
        r = c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "198.51.100.7"},
        )
        assert r.status_code == 403
        assert "not in pat allowlist" in r.json().get("detail", "")

        # In-range IP is allowed.
        r = c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "203.0.113.42"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["key_name"] == "pat:ci-bot"


def test_pat_ip_allowlist_update_and_clear(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "ops"},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        pat_id = r.json()["id"]
        secret = r.json()["secret"]
        assert r.json()["ip_cidrs"] == []

        # Initially the PAT works from any IP.
        assert c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "8.8.8.8"},
        ).status_code == 200

        # Pin to a single host.
        r = c.put(
            f"/keys/{pat_id}/ip-allowlist",
            json={"cidrs": ["10.0.0.5/32", "10.0.0.5/32"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        # Duplicates collapse, host suffix normalises.
        assert r.json()["ip_cidrs"] == ["10.0.0.5/32"]

        # Off-range is now denied.
        assert c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "8.8.8.8"},
        ).status_code == 403

        # /v1 alias of the same setter also works and clears the list.
        r = c.put(
            f"/v1/keys/{pat_id}/ip-allowlist",
            json={"cidrs": []},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200
        assert r.json()["ip_cidrs"] == []

        # Cleared: any IP allowed again.
        assert c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "8.8.8.8"},
        ).status_code == 200


def test_pat_ip_allowlist_rejects_bad_cidr(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "bad", "ip_cidrs": ["not-a-cidr"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 400
        assert "invalid ip_cidrs" in r.json()["detail"]

        # And the PATCH path validates too.
        r = c.post(
            "/keys",
            json={"name": "good"},
            headers={"X-API-Key": "opskey"},
        )
        pat_id = r.json()["id"]
        r = c.put(
            f"/keys/{pat_id}/ip-allowlist",
            json={"cidrs": ["999.0.0.0/8"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 400
        assert "invalid ip_cidrs" in r.json()["detail"]
