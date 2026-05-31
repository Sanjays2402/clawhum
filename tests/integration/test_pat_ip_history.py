"""Per-PAT IP history admin endpoint: tenant isolation and 404 shape.

A leaked personal access token is an enterprise incident. Operators
must be able to see every source IP that has successfully used a
token without being able to peek at any other tenant's token history.
These tests prove:

* a workspace admin sees their own token's distinct-IP timeline,
* the count grows on repeat calls from the same IP rather than
  duplicating rows,
* a second authenticated IP appears as a separate row,
* an admin from a different workspace gets HTTP 404 when probing the
  same key_id (no leak of token existence across tenants), and
* a reader role in the owning workspace is denied with HTTP 403.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_PAT_IP_HISTORY_PATH", str(tmp_path / "pat_ip_history.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmeadmin:10000:admin:acme,"
        "acme_reader:acmereader:10000:reader:acme,"
        "globex_admin:globexadmin:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import pat_ip_history

    pat_ip_history.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _mint(c, owner_header_key: str) -> tuple[str, str]:
    r = c.post(
        "/keys",
        json={"name": "ci", "roles": ["writer"]},
        headers={"X-API-Key": owner_header_key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["secret"]


def test_history_grows_and_distinguishes_ips(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        pat_id, secret = _mint(c, "acmeadmin")

        for _ in range(2):
            r = c.get(
                "/me",
                headers={"X-API-Key": secret, "X-Forwarded-For": "203.0.113.10"},
            )
            assert r.status_code == 200, r.text

        r = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "198.51.100.7",
                "User-Agent": "ClawHumTests/1.0",
            },
        )
        assert r.status_code == 200, r.text

        r = c.get(
            f"/keys/{pat_id}/ip-history",
            headers={"X-API-Key": "acmeadmin"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == pat_id
        assert body["distinct_ips"] == 2
        assert body["truncated"] is False
        ips = {row["ip"]: row for row in body["items"]}
        assert set(ips.keys()) == {"203.0.113.10", "198.51.100.7"}
        assert ips["203.0.113.10"]["count"] == 2
        assert ips["198.51.100.7"]["count"] == 1
        assert ips["198.51.100.7"]["last_ua"].startswith("ClawHumTests/1.0")
        assert ips["203.0.113.10"]["first_seen"] > 0
        assert (
            ips["203.0.113.10"]["last_seen"]
            >= ips["203.0.113.10"]["first_seen"]
        )


def test_cross_tenant_admin_cannot_read_history(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        pat_id, secret = _mint(c, "acmeadmin")
        r = c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "203.0.113.10"},
        )
        assert r.status_code == 200, r.text

        r = c.get(
            f"/keys/{pat_id}/ip-history",
            headers={"X-API-Key": "globexadmin"},
        )
        assert r.status_code == 404, r.text

        r = c.get("/keys", headers={"X-API-Key": "globexadmin"})
        assert r.status_code == 200, r.text
        assert r.json() == []


def test_reader_role_is_forbidden(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        pat_id, secret = _mint(c, "acmeadmin")
        r = c.get(
            "/me",
            headers={"X-API-Key": secret, "X-Forwarded-For": "203.0.113.10"},
        )
        assert r.status_code == 200, r.text

        r = c.get(
            f"/keys/{pat_id}/ip-history",
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403, r.text


def test_unknown_key_id_returns_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get(
            "/keys/does_not_exist/ip-history",
            headers={"X-API-Key": "acmeadmin"},
        )
        assert r.status_code == 404, r.text
