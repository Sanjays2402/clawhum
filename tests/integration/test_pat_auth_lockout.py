"""Per-IP brute-force lockout on personal-access-token authentication.

Proves the contracts a security reviewer expects:

1. After the configured number of failed pat_-prefixed auth attempts
   from the same source IP inside the window, subsequent attempts
   from that IP return HTTP 429 with a Retry-After header even when
   the next secret presented would have been valid.
2. The admin overview at GET /admin/pat-auth-lockout exposes the
   active lock so a workspace admin can see the attack in progress.
3. Cross-tenant isolation: an admin in workspace "beta" cannot see
   the locked IPs surfaced for an attack against workspace "acme"
   PATs, and DELETE on that IP returns 404 from beta.
4. The legitimate workspace admin can DELETE
   /admin/pat-auth-lockout/{ip} (MFA-gated) to clear the lock, the
   action is written to the audit log, and PAT auth from that IP
   immediately succeeds again.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, threshold: int = 3):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_PATH", str(tmp_path / "mfa.jsonl"))
    monkeypatch.setenv("CLAWHUM_MFA_LOCKOUT_PATH", str(tmp_path / "mfa_lockout.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_PAT_AUTH_LOCKOUT_PATH", str(tmp_path / "pat_auth_lockout.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ip_allowlist.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:acmeadmin:10000:admin:acme,beta:betaadmin:10000:admin:beta",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_PAT_AUTH_LOCKOUT_THRESHOLD", str(threshold))
    monkeypatch.setenv("CLAWHUM_PAT_AUTH_LOCKOUT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("CLAWHUM_PAT_AUTH_LOCKOUT_COOLDOWN_SECONDS", "120")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _enroll_mfa(c, key):
    r = c.post("/mfa/enroll", headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    from clawhum_api.mfa import totp
    r = c.post(
        "/mfa/verify",
        json={"code": totp(secret)},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200, r.text
    return secret


def _mint_pat(c, key, *, name: str) -> str:
    r = c.post(
        "/keys",
        json={"name": name, "roles": ["reader"]},
        headers={"X-API-Key": key},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["secret"]


def test_pat_brute_force_locks_ip_and_blocks_valid_secret(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, threshold=3)
    valid = _mint_pat(c, "acmeadmin", name="ci-bot")
    # 3 bad attempts trip the lock; the 4th must 429 even though it
    # presents the valid secret.
    for _ in range(3):
        bad = c.get("/me", headers={"X-API-Key": "pat_clearlyWrong000000000000000"})
        assert bad.status_code == 401, bad.text
    locked = c.get("/me", headers={"X-API-Key": valid})
    assert locked.status_code == 429, locked.text
    assert int(locked.headers.get("Retry-After", "0")) > 0
    assert "pat_auth_locked" in locked.json().get("detail", "")


def test_lockout_overview_and_admin_unlock_clears(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, threshold=2)
    secret = _enroll_mfa(c, "acmeadmin")
    valid = _mint_pat(c, "acmeadmin", name="ci-bot")
    # Trip the lock.
    for _ in range(2):
        c.get("/me", headers={"X-API-Key": "pat_anotherWrongSecret00000000"})
    locked = c.get("/me", headers={"X-API-Key": valid})
    assert locked.status_code == 429

    # Admin overview shows the lock.
    overview = c.get(
        "/admin/pat-auth-lockout",
        headers={"X-API-Key": "acmeadmin"},
    )
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["settings"]["threshold"] == 2
    assert len(body["locks"]) == 1
    locked_ip = body["locks"][0]["ip"]

    # Admin unlocks (MFA-gated).
    from clawhum_api.mfa import totp
    unlock = c.request(
        "DELETE",
        f"/admin/pat-auth-lockout/{locked_ip}",
        json={"reason": "cleared after support ticket"},
        headers={"X-API-Key": "acmeadmin", "X-MFA-Code": totp(secret)},
    )
    assert unlock.status_code == 200, unlock.text
    assert unlock.json()["locks"] == []

    # Valid PAT now succeeds again.
    ok = c.get("/me", headers={"X-API-Key": valid})
    assert ok.status_code == 200, ok.text

    # Audit event written.
    audit_lines = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]
    actions = [r.get("action") for r in audit_lines]
    assert "pat_auth_lockout.unlock" in actions


def test_cross_tenant_isolation_filters_attributed_locks(monkeypatch, tmp_path):
    """Locks tagged with a tenant are hidden from other workspaces.

    Unknown-tenant locks (the common case for an anonymous attack)
    are intentionally visible to every admin so any tenant can react
    to a probe. Once a lock has been attributed to a specific
    workspace (via admin tagging or a successful auth that follows
    the failures), it is scoped to that workspace only.
    """
    c = _client(monkeypatch, tmp_path, threshold=2)
    # Directly attribute a synthetic failure to acme by writing a
    # tenant-tagged row, then trip the lock with one more failure.
    from clawhum_api import pat_auth_lockout
    pat_auth_lockout.record_failure("10.0.0.99", tenant_id="acme")
    pat_auth_lockout.record_failure("10.0.0.99", tenant_id="acme")
    state = pat_auth_lockout.lock_state("10.0.0.99")
    assert state.locked, state
    assert state.last_tenant_id == "acme"

    # Beta admin cannot see acme's attributed lock.
    beta_overview = c.get(
        "/admin/pat-auth-lockout",
        headers={"X-API-Key": "betaadmin"},
    )
    assert beta_overview.status_code == 200, beta_overview.text
    assert beta_overview.json()["locks"] == []

    # Acme admin sees it.
    acme_overview = c.get(
        "/admin/pat-auth-lockout",
        headers={"X-API-Key": "acmeadmin"},
    )
    assert acme_overview.status_code == 200, acme_overview.text
    acme_locks = acme_overview.json()["locks"]
    assert any(l["ip"] == "10.0.0.99" for l in acme_locks)

    # Beta admin cannot unlock acme's IP (returns 404 because the
    # tenant filter hides the lock).
    beta_unlock = c.request(
        "DELETE",
        "/admin/pat-auth-lockout/10.0.0.99",
        json={"reason": "probe"},
        headers={"X-API-Key": "betaadmin"},
    )
    assert beta_unlock.status_code == 404, beta_unlock.text
