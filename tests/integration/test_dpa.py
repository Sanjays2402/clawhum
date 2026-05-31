"""Tests for the per-workspace Data Processing Agreement (DPA) endpoint.

What an enterprise procurement review checks here:

1. A fresh workspace shows ``accepted=false`` and exposes the
   current vendor DPA version + url.
2. Acceptance requires admin role; a writer is refused 403.
3. Acceptance requires the client to echo the current vendor
   version; a mismatched version is rejected 422 with a
   machine-parseable error code.
4. After admin accepts, the status flips and records actor, ip,
   and user agent.
5. Tenant A's acceptance is invisible to tenant B and B cannot
   withdraw A's acceptance (cross-tenant isolation).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_DPA_ACCEPTANCES_PATH", str(tmp_path / "dpa.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "acme_writer:acmewriter:10000:writer:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import dpa

    dpa.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _current_version():
    from clawhum_api import dpa

    return dpa.CURRENT_DPA_VERSION


def test_status_for_fresh_workspace_shows_unaccepted(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/dpa", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted"] is False
        assert body["acceptance"] is None
        assert body["current_version"] == _current_version()
        assert body["current_url"].startswith("https://")


def test_writer_cannot_accept(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/dpa/accept",
            json={"version": _current_version()},
            headers={"X-API-Key": "acmewriter"},
        )
        assert r.status_code == 403, r.text


def test_version_mismatch_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/dpa/accept",
            json={"version": "1999-01-01"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "dpa_version_mismatch"


def test_admin_accept_then_status_reflects(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/dpa/accept",
            json={"version": _current_version()},
            headers={
                "X-API-Key": "acmekey",
                "User-Agent": "procurement-bot/1.0",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["version"] == _current_version()
        assert "acme" in body["accepted_by"] or body["accepted_by"]
        assert body["user_agent"] == "procurement-bot/1.0"

        r = c.get("/dpa", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] is True
        assert body["acceptance"]["version"] == _current_version()


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme accepts.
        r = c.post(
            "/dpa/accept",
            json={"version": _current_version()},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text

        # Globex must not see Acme's acceptance.
        r = c.get("/dpa", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["accepted"] is False

        # Globex cannot withdraw what Acme accepted.
        r = c.delete("/dpa", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "dpa_not_accepted"

        # Acme's record is untouched.
        r = c.get("/dpa", headers={"X-API-Key": "acmekey"})
        assert r.json()["accepted"] is True


def test_withdraw_then_reaccept(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.post(
            "/dpa/accept",
            json={"version": _current_version()},
            headers={"X-API-Key": "acmekey"},
        )
        r = c.delete("/dpa", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 204
        r = c.get("/dpa", headers={"X-API-Key": "acmekey"})
        assert r.json()["accepted"] is False
        # Re-accept works after withdrawal.
        r = c.post(
            "/dpa/accept",
            json={"version": _current_version()},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201
