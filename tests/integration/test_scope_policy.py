"""Per-workspace PAT scope policy enforcement tests.

What an enterprise security review checks here:

1. With no policy, mint behaviour is unchanged (opt-in feature).
2. With a policy, an out-of-policy scope is rejected at /keys
   with HTTP 403 and a machine-parseable error code.
3. The /keys/policy surface advertises the workspace pin so the UI
   never offers a scope the server will reject.
4. Tenant A's policy is invisible to tenant B and does not block
   tenant B's mints (no cross-tenant leakage at the policy layer).
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
        "CLAWHUM_SCOPE_POLICY_PATH", str(tmp_path / "scope_policy.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import scope_policy

    scope_policy.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_no_policy_means_no_restriction(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/scope-policy", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is False
        assert body["scopes"] == []

        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["admin"], "scopes": ["write:keys"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert "write:keys" in r.json()["scopes"]


def test_policy_blocks_out_of_policy_mint(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Admin pins workspace to read-only scopes
        r = c.put(
            "/scope-policy",
            json={"scopes": ["read:matches", "read:library"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is True
        assert sorted(body["scopes"]) == ["read:library", "read:matches"]

        # Mint asking for write:keys must be rejected with 403
        r = c.post(
            "/keys",
            json={"name": "bad", "roles": ["admin"], "scopes": ["write:keys"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "scope_not_allowed"
        assert "write:keys" in detail["denied"]

        # Mint inside the policy succeeds
        r = c.post(
            "/keys",
            json={"name": "ok", "roles": ["admin"], "scopes": ["read:matches"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["scopes"] == ["read:matches"]


def test_policy_clamps_default_scopes_for_admin(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/scope-policy",
            json={"scopes": ["read:matches"]},
            headers={"X-API-Key": "acmekey"},
        )
        # No explicit scopes -> would default to "every scope admin
        # allows" (i.e. all). Under an active workspace policy, the
        # stored scopes must be clamped to the policy set.
        r = c.post(
            "/keys",
            json={"name": "default", "roles": ["admin"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["scopes"] == ["read:matches"]


def test_keys_policy_exposes_workspace_pin(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/scope-policy",
            json={"scopes": ["read:matches", "read:library"]},
            headers={"X-API-Key": "acmekey"},
        )
        r = c.get("/keys/policy", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workspace_scope_policy_enforcing"] is True
        assert sorted(body["workspace_scope_policy"]) == [
            "read:library",
            "read:matches",
        ]
        # Allowed scopes are role ∩ workspace policy
        assert sorted(body["allowed_scopes"]) == ["read:library", "read:matches"]


def test_no_cross_tenant_leakage(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme pins a tight policy
        c.put(
            "/scope-policy",
            json={"scopes": ["read:matches"]},
            headers={"X-API-Key": "acmekey"},
        )
        # Globex must NOT see acme's policy
        r = c.get("/scope-policy", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is False
        # Globex can still mint freely
        r = c.post(
            "/keys",
            json={"name": "g", "roles": ["admin"], "scopes": ["write:keys"]},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text
        assert "write:keys" in r.json()["scopes"]


def test_clear_policy_returns_to_unrestricted(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/scope-policy",
            json={"scopes": ["read:matches"]},
            headers={"X-API-Key": "acmekey"},
        )
        r = c.put(
            "/scope-policy",
            json={"scopes": []},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        assert r.json()["enforcing"] is False
        r = c.post(
            "/keys",
            json={"name": "wide", "roles": ["admin"], "scopes": ["write:keys"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert "write:keys" in r.json()["scopes"]
