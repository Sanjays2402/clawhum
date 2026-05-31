"""Step-up MFA gates destructive admin endpoints.

Proves the three contracts an enterprise reviewer cares about:

1. Before enrollment, admin destructive endpoints work as before
   (adoption is per-actor, the gate is open until you opt in).
2. After enrollment + verification, the same endpoint returns 401
   when no ``X-MFA-Code`` is presented, and 403 for a bad code.
3. A valid TOTP code lets the call through, and a recovery code is
   single-use.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_PATH", str(tmp_path / "mfa.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ip_allowlist.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:admin:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _mint_pat(c, name="ci"):
    r = c.post("/keys", json={"name": name}, headers={"X-API-Key": "opskey"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_mfa_gates_admin_destructive_endpoints(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Status: not enrolled.
        r = c.get("/mfa/status", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        assert r.json()["enrolled"] is False

        # Before enrollment the gate is open: revoke a PAT works with
        # just the admin role.
        pat_id = _mint_pat(c, "pre-mfa")
        r = c.delete(f"/keys/{pat_id}", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200, r.text

        # Enroll.
        r = c.post("/mfa/enroll", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200, r.text
        body = r.json()
        secret = body["secret"]
        assert body["otpauth"].startswith("otpauth://totp/")

        from clawhum_api.mfa import totp

        # Verify with a fresh TOTP.
        r = c.post(
            "/mfa/verify",
            json={"code": totp(secret)},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        recovery = r.json()["recovery_codes"]
        assert len(recovery) == 10

        # Now the same destructive endpoint demands a code.
        pat_id2 = _mint_pat(c, "needs-mfa")
        r = c.delete(f"/keys/{pat_id2}", headers={"X-API-Key": "opskey"})
        assert r.status_code == 401, r.text
        assert r.headers.get("www-authenticate") == "MFA"

        # Wrong code: 403.
        r = c.delete(
            f"/keys/{pat_id2}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": "000000"},
        )
        assert r.status_code == 403

        # Correct TOTP: passes.
        r = c.delete(
            f"/keys/{pat_id2}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": totp(secret)},
        )
        assert r.status_code == 200, r.text

        # Recovery code is one-shot.
        pat_id3 = _mint_pat(c, "recovery")
        rc = recovery[0]
        r = c.delete(
            f"/keys/{pat_id3}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": rc},
        )
        assert r.status_code == 200, r.text

        # Re-using the same recovery code must fail.
        pat_id4 = _mint_pat(c, "replay")
        r = c.delete(
            f"/keys/{pat_id4}",
            headers={"X-API-Key": "opskey", "X-MFA-Code": rc},
        )
        assert r.status_code == 403


def test_mfa_disable_requires_code(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/mfa/enroll", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        secret = r.json()["secret"]
        from clawhum_api.mfa import totp

        r = c.post(
            "/mfa/verify",
            json={"code": totp(secret)},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200

        # No valid code: rejected.
        r = c.request(
            "DELETE",
            "/mfa",
            json={"code": "000000"},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 403

        # Valid code: disabled.
        r = c.request(
            "DELETE",
            "/mfa",
            json={"code": totp(secret)},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200

        r = c.get("/mfa/status", headers={"X-API-Key": "opskey"})
        assert r.json()["enrolled"] is False
