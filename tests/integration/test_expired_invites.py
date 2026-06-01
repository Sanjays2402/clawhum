"""Expired-invite listing and bulk purge for stale pending invites.

Compliance reviewers ask workspace owners to identify pending invite
tokens that nobody ever accepted (mistyped email, recipient left the
company before clicking the link). The token is already useless once
``invite_expires_at`` elapses, but the seat row stays in the roster
until an admin revokes it row by row. This module covers the
list + bulk-purge endpoints, dry-run support, tenant isolation, and
the idempotency of repeated purges.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
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


def _invite_expiring(c, *, email, ttl_hours, key="acmekey"):
    r = c.post(
        "/members/invite",
        json={"email": email, "role": "reader", "ttl_hours": ttl_hours},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _force_expire(member_id: str) -> None:
    """Rewrite the member row in place so its invite_expires_at is in the past."""
    from clawhum_api import member_store

    rows = member_store._load_all()
    m = rows[member_id]
    expired = member_store.Member(
        id=m.id,
        tenant_id=m.tenant_id,
        email=m.email,
        role=m.role,
        status=m.status,
        invited_by=m.invited_by,
        invited_at=m.invited_at,
        accepted_at=m.accepted_at,
        invite_token_hash=m.invite_token_hash,
        invite_expires_at=time.time() - 3600,
    )
    member_store._append(member_store.asdict(expired))


def test_expired_invites_list_and_purge(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        fresh_id = _invite_expiring(c, email="alive@acme.test", ttl_hours=24)
        stale_id = _invite_expiring(c, email="stale@acme.test", ttl_hours=24)
        _force_expire(stale_id)

        # Reader can view the backlog (read-only governance view).
        r = c.get("/members/expired-invites", headers={"X-API-Key": "acmereader"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        assert body["members"][0]["id"] == stale_id
        assert body["members"][0]["email"] == "stale@acme.test"

        # Reader cannot purge.
        r = c.post(
            "/members/expired-invites/purge",
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403

        # Dry-run preview: does not mutate, returns the candidate set.
        r = c.post(
            "/members/expired-invites/purge?dry_run=true",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True
        assert body["would_delete"]["kind"] == "members.expired_invites"
        assert body["would_delete"]["count"] == 1
        assert body["would_delete"]["would_purge"][0]["id"] == stale_id
        # Still listed after dry-run.
        r = c.get("/members/expired-invites", headers={"X-API-Key": "acmekey"})
        assert r.json()["count"] == 1

        # Real purge tombstones the row.
        r = c.post(
            "/members/expired-invites/purge",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        assert body["purged"][0]["id"] == stale_id
        assert body["purged"][0]["status"] == "revoked"

        # Idempotent: the backlog is empty on the next call.
        r = c.post(
            "/members/expired-invites/purge",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        assert r.json() == {"purged": [], "count": 0}

        # The fresh invite is untouched and still visible in the main roster.
        r = c.get("/members", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        kept_ids = {m["id"] for m in r.json()["members"]}
        assert fresh_id in kept_ids
        assert stale_id not in kept_ids


def test_expired_invites_are_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        acme_stale = _invite_expiring(c, email="ghost@acme.test", ttl_hours=24, key="acmekey")
        globex_stale = _invite_expiring(c, email="ghost@globex.test", ttl_hours=24, key="globexkey")
        _force_expire(acme_stale)
        _force_expire(globex_stale)

        r = c.get("/members/expired-invites", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["members"]]
        assert ids == [acme_stale]

        # Acme admin can only purge their own backlog; globex row survives.
        r = c.post(
            "/members/expired-invites/purge",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        assert [m["id"] for m in r.json()["purged"]] == [acme_stale]

        r = c.get("/members/expired-invites", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["members"]]
        assert ids == [globex_stale]
