"""System-use notification ack gate enforcement tests.

What an enterprise security review checks here:

1. With no banner, every mutating route works as before (opt-in).
2. With an enforced banner, an unacked actor's mutating call is
   rejected with HTTP 403 ``system_use_ack_required`` and the
   diagnostic headers needed by SDKs.
3. After acking, the same actor can mutate.
4. Changing the wording bumps the revision, invalidating the ack.
5. Tenant isolation: tenant A's banner does not gate tenant B and
   tenant A's actor ack does not leak to tenant B's acks list.
6. The ack endpoint rejects a stale revision with HTTP 409.
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
        "CLAWHUM_SYSTEM_USE_NOTIFICATION_PATH",
        str(tmp_path / "system_use_notification.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_SYSTEM_USE_ACKS_PATH",
        str(tmp_path / "system_use_acks.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "acme_writer:acmewrite:10000:writer:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import system_use_notification

    system_use_notification.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _set_banner(c, key, *, title="Authorized use only", body="All activity is logged.", enforced=True):
    return c.put(
        "/system-use-notification",
        json={"title": title, "body": body, "enforced": enforced},
        headers={"X-API-Key": key},
    )


def test_no_banner_allows_mutations(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/system-use-notification", headers={"X-API-Key": "acmewrite"})
        assert r.status_code == 200, r.text
        assert r.json()["needs_ack"] is False

        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_enforced_banner_blocks_unacked_mutation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert _set_banner(c, "acmekey").status_code == 200

        # An unacked writer cannot mutate.
        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "acmewrite"},
        )
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["detail"]["code"] == "system_use_ack_required" if isinstance(body.get("detail"), dict) else body["code"] == "system_use_ack_required"
        assert r.headers.get("X-System-Use-Ack-Required") == "1"
        assert r.headers.get("X-System-Use-Notification-Revision") == "1"

        # Ack and retry.
        r = c.post(
            "/system-use-notification/ack",
            json={"revision": 1},
            headers={"X-API-Key": "acmewrite"},
        )
        assert r.status_code == 200, r.text

        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "acmewrite"},
        )
        assert r.status_code == 200, r.text


def test_wording_change_bumps_revision_and_invalidates_ack(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert _set_banner(c, "acmekey", body="v1").status_code == 200
        c.post(
            "/system-use-notification/ack",
            json={"revision": 1},
            headers={"X-API-Key": "acmewrite"},
        )

        # Update wording, revision bumps to 2.
        r = _set_banner(c, "acmekey", body="v2 with new clauses")
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 2

        # Stale ack at revision 1 is rejected with 409.
        r = c.post(
            "/system-use-notification/ack",
            json={"revision": 1},
            headers={"X-API-Key": "acmewrite"},
        )
        assert r.status_code == 409, r.text

        # Mutating call is once again blocked until re-ack.
        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "acmewrite"},
        )
        assert r.status_code == 403, r.text


def test_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert _set_banner(c, "acmekey").status_code == 200

        # Globex has no banner: their mutation passes.
        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text

        # Acme writer acks their own banner.
        c.post(
            "/system-use-notification/ack",
            json={"revision": 1},
            headers={"X-API-Key": "acmewrite"},
        )

        # Globex admin ack list must be empty (no cross-tenant leak).
        r = c.get(
            "/system-use-notification/acks",
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == []

        # Acme admin sees their own ack entry.
        r = c.get(
            "/system-use-notification/acks",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["actor_id"] == "acme_writer"
        assert rows[0]["revision"] == 1
