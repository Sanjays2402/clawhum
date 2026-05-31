from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, keys_spec: str | None = None):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_SEAT_LIMITS_PATH", str(tmp_path / "seats.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_SSO_STORE_PATH", str(tmp_path / "sso.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        keys_spec
        or "acme_admin:acmekey:10000:admin:acme,"
        "acme_reader:acmereader:10000:reader:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_seat_limit_default_is_unlimited(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/workspace/seat-limit", headers={"X-API-Key": "acmereader"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == "acme"
        assert body["limit"] == 0
        assert body["used"] == 0
        assert body["remaining"] == -1


def test_seat_limit_set_requires_admin_with_mfa(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Reader cannot set limit.
        r = c.put(
            "/workspace/seat-limit",
            json={"limit": 5},
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403

        # Admin without MFA enrollment is allowed (no enrollment => no challenge).
        r = c.put(
            "/workspace/seat-limit",
            json={"limit": 3},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["limit"] == 3
        assert body["used"] == 0
        assert body["remaining"] == 3
        assert body["updated_by"] == "acme_admin"


def test_invite_blocked_when_seat_cap_reached(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Set tiny cap.
        r = c.put(
            "/workspace/seat-limit",
            json={"limit": 1},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200

        # First invite fits.
        r = c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text

        # Second invite hits the cap; 402 Payment Required with structured body.
        r = c.post(
            "/members/invite",
            json={"email": "bob@acme.test", "role": "reader"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 402, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "seat_limit_exceeded"
        assert detail["current"] == 1
        assert detail["limit"] == 1

        # Usage view reflects the consumed pending invite.
        r = c.get("/workspace/seat-limit", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        body = r.json()
        assert body["limit"] == 1
        assert body["used"] == 1
        assert body["remaining"] == 0


def test_seat_limit_is_workspace_scoped(monkeypatch, tmp_path):
    """Capping acme must not affect globex; cross-tenant isolation."""
    with _client(monkeypatch, tmp_path) as c:
        # Cap acme at 0 effective via low value, fill it.
        r = c.put(
            "/workspace/seat-limit",
            json={"limit": 1},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        # Globex has no cap; invites should still flow.
        r = c.post(
            "/members/invite",
            json={"email": "carol@globex.test", "role": "writer"},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 201, r.text

        # Globex view of its own seat limit is untouched.
        r = c.get("/workspace/seat-limit", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        body = r.json()
        assert body["tenant_id"] == "globex"
        assert body["limit"] == 0
        assert body["used"] == 1


def test_seat_limit_rejects_unscoped_keys(monkeypatch, tmp_path):
    # Open auth => caller has no workspace; seat license endpoint must refuse.
    with _client(monkeypatch, tmp_path, keys_spec="") as c:
        r = c.get("/workspace/seat-limit")
        # When auth is open, the request still reaches the route but the
        # tenant guard rejects anon/dev tenants.
        assert r.status_code in (401, 403)
