"""PAT last-used forensic breadcrumbs (IP + User-Agent).

When a personal access token leaks, the first question an operator
asks is "where was it last used from?" These tests prove the keys
listing exposes the resolved client IP and User-Agent of the most
recent successful authentication, and that an unrelated tenant
listing their own keys cannot see another workspace's breadcrumbs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:acmekey:10000:writer:acme,globex:globexkey:10000:writer:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_pat_last_used_records_resolved_ip_and_truncated_ua(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/keys", json={"name": "ci"}, headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        secret = body["secret"]
        pat_id = body["id"]
        # Freshly minted PAT has no usage breadcrumbs yet.
        assert body["last_used_at"] == 0.0
        assert body["last_used_ip"] == ""
        assert body["last_used_ua"] == ""

        # A real call from a known IP + UA records both.
        long_ua = "ClawHumCI/1.0 " + ("x" * 500)
        r = c.get(
            "/me",
            headers={
                "X-API-Key": secret,
                "X-Forwarded-For": "203.0.113.77",
                "User-Agent": long_ua,
            },
        )
        assert r.status_code == 200, r.text

        # The owner sees the breadcrumb on their key listing.
        r = c.get("/keys", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()}
        row = rows[pat_id]
        assert row["last_used_at"] > 0
        assert row["last_used_ip"] == "203.0.113.77"
        assert row["last_used_ua"].startswith("ClawHumCI/1.0")
        # User-Agent is bounded so a hostile client cannot bloat storage.
        assert len(row["last_used_ua"]) <= 200


def test_pat_last_used_isolated_across_tenants(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme mints and uses a PAT.
        r = c.post("/keys", json={"name": "acme-ci"}, headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        acme_secret = r.json()["secret"]
        r = c.get(
            "/me",
            headers={
                "X-API-Key": acme_secret,
                "X-Forwarded-For": "203.0.113.10",
                "User-Agent": "acme-runner",
            },
        )
        assert r.status_code == 200

        # Globex (a different workspace) listing its own keys must
        # never see Acme's breadcrumbs. Globex has no PATs of its own
        # so the listing is empty; cross-tenant leakage would show
        # Acme's PAT in this response.
        r = c.get("/keys", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        globex_rows = r.json()
        assert globex_rows == []

        # Sanity: Acme can still see its own breadcrumb.
        r = c.get("/keys", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["last_used_ip"] == "203.0.113.10"
        assert rows[0]["last_used_ua"] == "acme-runner"
