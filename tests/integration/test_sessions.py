"""Tests for active session tracking and force-logout.

Covers the guarantees enterprise procurement checks:

1. An authenticated request creates a session row scoped to the
   caller's tenant; another tenant's admin cannot see it.
2. ``revoke-all`` for an actor force-logs-out their other clients
   (the next request from the revoked client returns 401) while the
   operator's own session is preserved.
3. The workspace policy clamps newly minted PAT lifetimes; an
   ``expires_in_days`` value above the cap is silently tightened.
4. The session policy itself cannot be read across tenants and a
   tenant B policy update does not leak into tenant A.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ipa.jsonl"))
    monkeypatch.setenv("CLAWHUM_SESSIONS_PATH", str(tmp_path / "sessions.jsonl"))
    monkeypatch.setenv("CLAWHUM_SESSION_POLICY_PATH", str(tmp_path / "session_policy.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pat.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import ip_allowlist, sessions
    ip_allowlist.reset_cache()
    sessions.reset_cache_for_tests()
    from clawhum_api.app import create_app
    return TestClient(create_app())


_HDR_UA = {"User-Agent": "pytest-session-suite/1.0"}


def test_authenticated_request_creates_session_scoped_to_tenant(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "alpha:sk_admin_a:9999:admin:acme,beta:sk_admin_b:9999:admin:globex",
    )
    assert c.get("/me", headers={"X-API-Key": "sk_admin_a", **_HDR_UA}).status_code == 200
    assert c.get("/me", headers={"X-API-Key": "sk_admin_b", **_HDR_UA}).status_code == 200

    r = c.get("/sessions", headers={"X-API-Key": "sk_admin_a", **_HDR_UA})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["actor"] == "alpha"
    assert items[0]["is_current"] is True

    r2 = c.get("/sessions", headers={"X-API-Key": "sk_admin_b", **_HDR_UA})
    assert r2.status_code == 200
    items_b = r2.json()["items"]
    assert len(items_b) == 1
    assert items_b[0]["actor"] == "beta"
    # Critical: tenant A's session id is not visible to tenant B.
    a_ids = {s["id"] for s in items}
    b_ids = {s["id"] for s in items_b}
    assert a_ids.isdisjoint(b_ids)


def test_cannot_revoke_or_read_policy_across_tenants(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "alpha:sk_admin_a:9999:admin:acme,beta:sk_admin_b:9999:admin:globex",
    )
    c.get("/me", headers={"X-API-Key": "sk_admin_a", **_HDR_UA})
    a_session_id = c.get(
        "/sessions", headers={"X-API-Key": "sk_admin_a", **_HDR_UA}
    ).json()["items"][0]["id"]

    # Tenant B tries to delete tenant A's session by id. Must 404, not 204.
    bad = c.delete(
        f"/sessions/{a_session_id}",
        headers={"X-API-Key": "sk_admin_b", **_HDR_UA},
    )
    assert bad.status_code == 404

    # Tenant B's policy write must not affect tenant A.
    pb = c.put(
        "/sessions/policy",
        json={
            "idle_timeout_minutes": 5,
            "absolute_max_minutes": 30,
            "max_pat_lifetime_minutes": 10,
        },
        headers={"X-API-Key": "sk_admin_b", **_HDR_UA},
    )
    assert pb.status_code == 200, pb.text

    pa = c.get(
        "/sessions/policy", headers={"X-API-Key": "sk_admin_a", **_HDR_UA}
    ).json()
    assert pa["idle_timeout_minutes"] == 0
    assert pa["absolute_max_minutes"] == 0
    assert pa["max_pat_lifetime_minutes"] == 0


def test_revoke_all_for_actor_force_logs_out_other_sessions(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "alpha:sk_admin:9999:admin:acme")
    op_headers = {
        "X-API-Key": "sk_admin",
        "User-Agent": "operator-cli/2.0",
        "X-Forwarded-For": "203.0.113.10",
    }
    bad_headers = {
        "X-API-Key": "sk_admin",
        "User-Agent": "attacker-curl/0.1",
        "X-Forwarded-For": "198.51.100.7",
    }

    assert c.get("/me", headers=op_headers).status_code == 200
    assert c.get("/me", headers=bad_headers).status_code == 200

    listed = c.get("/sessions", headers=op_headers).json()["items"]
    assert len(listed) == 2

    r = c.post(
        "/sessions/revoke-all",
        json={"actor": "alpha", "reason": "suspected leak"},
        headers=op_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] >= 2

    # The attacker client is rejected on its very next request.
    blocked = c.get("/me", headers=bad_headers)
    assert blocked.status_code == 401
    assert "session" in blocked.json()["detail"].lower()

    # The operator's own session keeps working (include_self defaulted false).
    still_ok = c.get("/me", headers=op_headers)
    assert still_ok.status_code == 200


def test_workspace_policy_caps_pat_lifetime(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "alpha:sk_admin:9999:admin:acme")
    hdr = {"X-API-Key": "sk_admin", **_HDR_UA}
    r = c.put(
        "/sessions/policy",
        json={
            "idle_timeout_minutes": 0,
            "absolute_max_minutes": 0,
            "max_pat_lifetime_minutes": 60,
        },
        headers=hdr,
    )
    assert r.status_code == 200, r.text

    # Mint a PAT requesting 30-day lifetime; the cap must tighten it.
    pat = c.post(
        "/keys",
        json={"name": "ci-bot", "roles": ["reader"], "expires_in_days": 30},
        headers=hdr,
    )
    assert pat.status_code == 200, pat.text
    body = pat.json()
    import time as _t
    horizon = _t.time() + 60 * 60 + 5  # cap + small clock slack
    assert 0 < body["expires_at"] <= horizon, body


def test_max_pat_age_policy_rejects_aged_tokens(monkeypatch, tmp_path):
    """A PAT older than the workspace ``max_pat_age_minutes`` is
    rejected at auth with HTTP 401 and a deterministic detail string,
    even when its own ``expires_at`` would still permit it. Rotating
    the token resets the clock so the rotated secret authenticates.
    """
    c = _client(monkeypatch, tmp_path, "alpha:sk_admin:9999:admin:acme")
    hdr = {"X-API-Key": "sk_admin", **_HDR_UA}

    pat_resp = c.post(
        "/keys",
        json={"name": "old-ci", "roles": ["reader"], "expires_in_days": 365},
        headers=hdr,
    )
    assert pat_resp.status_code == 200, pat_resp.text
    pat_secret = pat_resp.json()["secret"]
    pat_id = pat_resp.json()["id"]

    # Backdate created_at by appending a record with an older timestamp.
    # The PAT store is an append-only JSONL last-writer-wins log.
    from clawhum_api import pat_store
    rec = pat_store.lookup_by_secret(pat_secret)
    assert rec is not None
    aged_created = rec.created_at - 2 * 3600
    p = pat_store._path()  # type: ignore[attr-defined]
    import json
    p.write_text(
        p.read_text()
        + json.dumps(
            {
                "id": rec.id,
                "name": rec.name,
                "tenant_id": rec.tenant_id,
                "roles": sorted(rec.roles),
                "rpm": rec.rpm,
                "secret_hash": rec.secret_hash,
                "secret_hint": rec.secret_hint,
                "scopes": sorted(rec.scopes),
                "expires_at": rec.expires_at,
                "ip_cidrs": sorted(rec.ip_cidrs),
                "last_used_at": rec.last_used_at,
                "last_used_ip": rec.last_used_ip,
                "last_used_ua": rec.last_used_ua,
                "prior_secret_hash": rec.prior_secret_hash,
                "prior_secret_hint": rec.prior_secret_hint,
                "prior_secret_expires_at": rec.prior_secret_expires_at,
                "created_at": aged_created,
            }
        )
        + "\n",
    )

    # Pre-policy the aged token still works (no max_pat_age set).
    pre = c.get("/me", headers={"X-API-Key": pat_secret, **_HDR_UA})
    assert pre.status_code == 200, pre.text

    # Set workspace policy: force-rotate after 60 minutes.
    r = c.put(
        "/sessions/policy",
        json={
            "idle_timeout_minutes": 0,
            "absolute_max_minutes": 0,
            "max_pat_lifetime_minutes": 0,
            "max_pat_age_minutes": 60,
        },
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    assert r.json()["max_pat_age_minutes"] == 60

    post = c.get("/me", headers={"X-API-Key": pat_secret, **_HDR_UA})
    assert post.status_code == 401, post.text
    assert "pat_aged_out" in post.json().get("detail", ""), post.json()

    # Admin API key (not a PAT) is unaffected by the PAT max-age rule.
    assert c.get("/me", headers=hdr).status_code == 200

    # /keys listing surfaces the aged_out flag for the UI badge.
    listing = c.get("/keys", headers=hdr)
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    aged_rows = [row for row in rows if row["id"] == pat_id]
    assert aged_rows and aged_rows[0]["aged_out"] is True
    assert aged_rows[0]["max_age_minutes"] == 60

    # Rotation refreshes created_at so the new secret authenticates.
    rot = c.post(
        f"/keys/{pat_id}/rotate",
        json={"grace_minutes": 0},
        headers=hdr,
    )
    assert rot.status_code == 200, rot.text
    new_secret = rot.json()["secret"]
    assert c.get("/me", headers={"X-API-Key": new_secret, **_HDR_UA}).status_code == 200
