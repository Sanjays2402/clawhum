"""Brute-force lockout on MFA-gated admin endpoints.

Proves the four contracts a security reviewer cares about:

1. After the configured number of consecutive bad MFA codes inside
   the window, the actor is locked out and further attempts return
   HTTP 429 with a Retry-After header. The destructive action is not
   performed.
2. A successful TOTP submission inside the window resets the counter,
   so a legitimate user who fat-fingers two digits before getting it
   right is not penalised.
3. Lockout state is per actor: locking actor A does not affect
   actor B holding a different API key in the same workspace.
4. A workspace admin can clear a peer's lockout through
   /admin/mfa/lockouts/unlock, the event is written to the audit
   chain, and the unlocked actor can immediately submit a valid code.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_PATH", str(tmp_path / "mfa.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_LOCKOUT_PATH", str(tmp_path / "mfa_lockout.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ip_allowlist.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:opskey:10000:admin:acme,acme:peerkey:10000:admin:acme",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_LOCKOUT_THRESHOLD", "3")
    monkeypatch.setenv("CLAWHUM_MFA_LOCKOUT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("CLAWHUM_MFA_LOCKOUT_COOLDOWN_SECONDS", "120")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _enroll(c, key):
    r = c.post("/mfa/enroll", headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    from clawhum_api.mfa import totp
    r = c.post("/mfa/verify", json={"code": totp(secret)}, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return secret


def _mint_pat(c, key, name):
    r = c.post("/keys", json={"name": name}, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_repeated_bad_mfa_codes_lock_actor_with_429_retry_after(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _enroll(c, "opskey")
        pat_id = _mint_pat(c, "opskey", "to-revoke")
        # Three bad codes (threshold=3) -> the third response must be a
        # 429 because that is the failure that trips the lock.
        for i in range(2):
            r = c.delete(
                f"/keys/{pat_id}",
                headers={"X-API-Key": "opskey", "X-MFA-Code": "000000"},
            )
            assert r.status_code == 403, (i, r.status_code, r.text)
        r = c.delete(
            f"/keys/{pat_id}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": "000000"},
        )
        assert r.status_code == 429, r.text
        retry = r.headers.get("Retry-After")
        assert retry is not None and int(retry) > 0

        # Even with a correct code the actor is locked: 429 again.
        from clawhum_api.mfa import actor_id_for, get as mfa_get
        rec = mfa_get(actor_id_for("opskey"))
        from clawhum_api.mfa import totp
        r = c.delete(
            f"/keys/{pat_id}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": totp(rec.secret)},
        )
        assert r.status_code == 429, r.text

        # Self-status endpoint reports the lock so the UI can render it.
        r = c.get("/mfa/lockout", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        body = r.json()
        assert body["locked"] is True
        assert body["retry_after"] > 0


def test_successful_mfa_clears_failure_counter(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        secret = _enroll(c, "opskey")
        pat_id = _mint_pat(c, "opskey", "stays-alive")
        # Two bad attempts, then a good one. The good one resets the
        # counter so two more bads after that should NOT trip the lock
        # (threshold is 3 consecutive within the window).
        for _ in range(2):
            r = c.delete(
                f"/keys/{pat_id}",
                headers={"X-API-Key": "opskey", "X-MFA-Code": "000000"},
            )
            assert r.status_code == 403
        # Good code: the request itself needs an actual mutable target.
        pat_id_b = _mint_pat(c, "opskey", "burn1")
        from clawhum_api.mfa import totp
        r = c.delete(
            f"/keys/{pat_id_b}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": totp(secret)},
        )
        assert r.status_code == 200, r.text
        # Two more bad attempts should still only be 403, not 429.
        pat_id_c = _mint_pat(c, "opskey", "burn2")
        for _ in range(2):
            r = c.delete(
                f"/keys/{pat_id_c}",
                headers={"X-API-Key": "opskey", "X-MFA-Code": "000000"},
            )
            assert r.status_code == 403, r.text


def test_lockout_is_per_actor_not_global(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _enroll(c, "opskey")
        _enroll(c, "peerkey")
        pat_a = _mint_pat(c, "opskey", "a")
        # Trip opskey.
        for _ in range(3):
            c.delete(
                f"/keys/{pat_a}",
                headers={"X-API-Key": "opskey", "X-MFA-Code": "000000"},
            )
        r_self = c.get("/mfa/lockout", headers={"X-API-Key": "opskey"})
        assert r_self.json()["locked"] is True
        # Peer is in the same workspace but a different actor; they are
        # NOT affected. Their /mfa/lockout reports not locked, and they
        # can perform a destructive op with their own valid TOTP.
        r_peer = c.get("/mfa/lockout", headers={"X-API-Key": "peerkey"})
        assert r_peer.json()["locked"] is False
        from clawhum_api.mfa import actor_id_for, get as mfa_get, totp
        peer_rec = mfa_get(actor_id_for("peerkey"))
        pat_b = _mint_pat(c, "peerkey", "b")
        r = c.delete(
            f"/keys/{pat_b}",
            headers={"X-API-Key": "peerkey", "X-MFA-Code": totp(peer_rec.secret)},
        )
        assert r.status_code == 200, r.text


def test_admin_can_unlock_peer_and_audit_records_it(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        secret_ops = _enroll(c, "opskey")
        _enroll(c, "peerkey")
        pat_p = _mint_pat(c, "peerkey", "victim")
        # Trip peerkey.
        for _ in range(3):
            c.delete(
                f"/keys/{pat_p}",
                headers={"X-API-Key": "peerkey", "X-MFA-Code": "000000"},
            )
        # Admin lists lockouts and finds peerkey's actor_id.
        from clawhum_api.mfa import totp, actor_id_for
        r = c.get(
            "/admin/mfa/lockouts",
            headers={"X-API-Key": "opskey", "X-MFA-Code": totp(secret_ops)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["threshold"] == 3
        peer_actor = actor_id_for("peerkey")
        ids = [item["actor_id"] for item in body["items"]]
        assert peer_actor in ids
        # Unlock.
        r = c.post(
            "/admin/mfa/lockouts/unlock",
            json={"actor_id": peer_actor, "reason": "user lost phone"},
            headers={"X-API-Key": "opskey", "X-MFA-Code": totp(secret_ops)},
        )
        assert r.status_code == 200, r.text
        assert r.json()["was_locked"] is True
        # Audit chain has an mfa.unlocked event.
        audit_path = tmp_path / "audit.jsonl"
        text = audit_path.read_text()
        assert any(
            json.loads(line).get("event") == "mfa.unlocked"
            for line in text.splitlines()
            if line.strip()
        )
        # Peerkey can immediately submit a valid code again.
        from clawhum_api.mfa import get as mfa_get
        peer_rec = mfa_get(peer_actor)
        pat_q = _mint_pat(c, "peerkey", "recovered")
        r = c.delete(
            f"/keys/{pat_q}",
            headers={"X-API-Key": "peerkey", "X-MFA-Code": totp(peer_rec.secret)},
        )
        assert r.status_code == 200, r.text
