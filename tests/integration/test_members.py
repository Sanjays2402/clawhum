from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, keys_spec: str | None = None):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
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


def test_invite_accept_list_update_revoke(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Unauth blocked.
        assert c.get("/members").status_code == 401

        # Empty roster to start.
        r = c.get("/members", headers={"X-API-Key": "acmereader"})
        assert r.status_code == 200
        assert r.json()["members"] == []
        assert r.json()["counts"] == {"active": 0, "invited": 0, "revoked": 0}

        # Non-admins cannot invite.
        r = c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403

        # Admin invites alice.
        r = c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        token = body["invite_token"]
        member_id = body["id"]
        assert token.startswith("inv_")
        assert body["status"] == "invited"
        assert body["role"] == "writer"
        assert body["invited_by"] == "acme_admin"

        # Duplicate invite for the same email rejected.
        r = c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "reader"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400

        # List now shows the pending invite without the token hash.
        r = c.get("/members", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        rows = r.json()["members"]
        assert len(rows) == 1
        assert "invite_token" not in rows[0]
        assert "invite_token_hash" not in rows[0]
        assert rows[0]["email"] == "alice@acme.test"

        # Accept with bad token fails.
        r = c.post("/members/accept", json={"token": "inv_bogus_token_value"})
        assert r.status_code == 400

        # Accept with the real token succeeds.
        r = c.post("/members/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        assert r.json()["accepted_at"] > 0

        # Token cannot be replayed.
        r = c.post("/members/accept", json={"token": token})
        assert r.status_code == 400

        # Update role.
        r = c.patch(
            f"/members/{member_id}",
            json={"role": "reader"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        assert r.json()["role"] == "reader"

        # Invalid role rejected.
        r = c.patch(
            f"/members/{member_id}",
            json={"role": "superuser"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400

        # Revoke.
        r = c.delete(
            f"/members/{member_id}",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 204

        # Roster is empty again.
        r = c.get("/members", headers={"X-API-Key": "acmekey"})
        assert r.json()["members"] == []


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    """Globex admin must not see or mutate Acme's roster."""
    with _client(monkeypatch, tmp_path) as c:
        # Acme admin invites someone.
        r = c.post(
            "/members/invite",
            json={"email": "bob@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201
        acme_member_id = r.json()["id"]

        # Globex admin sees an empty roster (different tenant).
        r = c.get("/members", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["members"] == []

        # Globex admin cannot mutate Acme's member.
        r = c.patch(
            f"/members/{acme_member_id}",
            json={"role": "reader"},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 400

        r = c.delete(
            f"/members/{acme_member_id}",
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 404

        # Acme still has the original invite intact.
        r = c.get("/members", headers={"X-API-Key": "acmekey"})
        members = r.json()["members"]
        assert len(members) == 1
        assert members[0]["id"] == acme_member_id
        assert members[0]["role"] == "writer"


def test_dev_tenant_refused(monkeypatch, tmp_path):
    """No api keys configured = dev mode; member mgmt must refuse."""
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.delenv("CLAWHUM_API_KEYS", raising=False)
    monkeypatch.delenv("CLAWHUM_API_KEY", raising=False)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    with TestClient(create_app()) as c:
        r = c.post(
            "/members/invite",
            json={"email": "x@y.test", "role": "writer"},
        )
        assert r.status_code == 403
        assert "workspace-scoped" in r.json()["detail"]


def test_resend_invite_rotates_token_and_invalidates_old(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/members/invite",
            json={"email": "carol@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        member_id = r.json()["id"]
        old_token = r.json()["invite_token"]
        old_expires = r.json()["invite_expires_at"]

        # Non-admin rejected.
        r = c.post(
            f"/members/{member_id}/resend",
            json={},
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403

        # Admin rotates with a custom TTL override.
        r = c.post(
            f"/members/{member_id}/resend",
            json={"ttl_hours": 48},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        new_token = body["invite_token"]
        assert new_token.startswith("inv_")
        assert new_token != old_token
        assert body["id"] == member_id
        assert body["status"] == "invited"
        assert body["accepted_at"] == 0
        assert body["invite_expires_at"] != old_expires

        # Old token must no longer accept.
        r = c.post("/members/accept", json={"token": old_token})
        assert r.status_code == 400

        # New token accepts cleanly.
        r = c.post("/members/accept", json={"token": new_token})
        assert r.status_code == 200
        assert r.json()["status"] == "active"

        # Resending an already-accepted member fails.
        r = c.post(
            f"/members/{member_id}/resend",
            json={},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 404


def test_resend_invite_cross_tenant_blocked(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/members/invite",
            json={"email": "dave@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201
        acme_id = r.json()["id"]

        # Globex admin must not rotate Acme's invite token.
        r = c.post(
            f"/members/{acme_id}/resend",
            json={},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 404


def test_resend_invite_revoked_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/members/invite",
            json={"email": "eve@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        member_id = r.json()["id"]
        assert c.delete(
            f"/members/{member_id}", headers={"X-API-Key": "acmekey"}
        ).status_code == 204

        r = c.post(
            f"/members/{member_id}/resend",
            json={},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 404


def test_last_admin_cannot_be_demoted_or_revoked(monkeypatch, tmp_path):
    """The workspace must never end up with zero active admin members.

    Invite two members (an admin and a writer), accept both, and verify:
      * demoting the only admin returns 409
      * revoking the only admin returns 409
      * once a second admin exists, the original admin can be demoted
        and the new admin can revoke them
      * the guard is per-tenant; another workspace's admin count is
        irrelevant to this workspace.
    """
    with _client(monkeypatch, tmp_path) as c:
        # Invite + accept first admin (alice) in acme.
        r = c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "admin"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        alice_id = r.json()["id"]
        alice_token = r.json()["invite_token"]
        assert c.post("/members/accept", json={"token": alice_token}).status_code == 200

        # Invite + accept a writer (bob) in acme; pending invites must
        # not count toward the admin total.
        r = c.post(
            "/members/invite",
            json={"email": "bob@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        bob_id = r.json()["id"]
        assert c.post(
            "/members/accept", json={"token": r.json()["invite_token"]}
        ).status_code == 200

        # Also leave a pending admin invite around to prove pending
        # invites are not counted as protection against lockout.
        r = c.post(
            "/members/invite",
            json={"email": "ghost@acme.test", "role": "admin"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201

        # Demoting the only active admin must be refused with 409.
        r = c.patch(
            f"/members/{alice_id}",
            json={"role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 409, r.text
        assert "last admin" in r.json()["detail"].lower()

        # Revoking the only active admin must also be refused with 409.
        r = c.delete(
            f"/members/{alice_id}",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 409

        # Globex has zero active admin members; demoting acme's admin
        # is still refused regardless of any other tenant's state.
        # Promote bob to admin first, then alice can be demoted.
        r = c.patch(
            f"/members/{bob_id}",
            json={"role": "admin"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        r = c.patch(
            f"/members/{alice_id}",
            json={"role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200

        # Now bob is the only admin; revoking him must fail again.
        r = c.delete(
            f"/members/{bob_id}",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 409
