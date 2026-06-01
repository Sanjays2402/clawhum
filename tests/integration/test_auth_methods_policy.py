"""Per-workspace allowed authentication methods enforcement tests.

What an enterprise security review checks here:

1. With no policy, every credential class works (opt-in feature).
2. With ``pat`` disabled, an existing PAT is rejected at auth with
   HTTP 401 and an ``X-Auth-Method-Disabled: pat`` header.
3. With ``pat`` disabled, /keys mint returns HTTP 403 and the same
   header so the UI can route the user to a runbook.
4. Disabling ``env_key`` does not block other tenants. Tenant
   isolation: tenant A's policy must not block tenant B's keys.
5. The PUT endpoint refuses an empty methods array so an admin
   cannot lock the workspace out by accident.
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
        "CLAWHUM_AUTH_METHODS_POLICY_PATH",
        str(tmp_path / "auth_methods_policy.jsonl"),
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
    from clawhum_api import auth_methods_policy

    auth_methods_policy.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_no_policy_means_every_method_allowed(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/auth-methods-policy", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is False
        assert sorted(body["effective_methods"]) == ["env_key", "pat", "scim"]

        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_pat_disabled_blocks_mint_and_auth(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Mint a PAT FIRST under the open policy.
        r = c.post(
            "/keys",
            json={"name": "deploy", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]

        # Confirm the PAT can authenticate right now.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text

        # Admin disables PATs for the workspace.
        r = c.put(
            "/auth-methods-policy",
            json={"methods": ["env_key", "scim"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is True

        # Subsequent PAT auth must now be rejected with 401 and the
        # machine-readable header naming the disabled method.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 401
        assert r.headers.get("X-Auth-Method-Disabled") == "pat"
        assert "auth_method_disabled" in r.text

        # New PAT mints must be rejected with 403 plus the same header.
        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 403, r.text
        assert r.headers.get("X-Auth-Method-Disabled") == "pat"
        assert "pat_minting_disabled" in r.text


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme disables PATs.
        r = c.put(
            "/auth-methods-policy",
            json={"methods": ["env_key", "scim"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Globex must still be able to mint PATs and read the
        # default-allowed policy view (no enforcement).
        r = c.get("/auth-methods-policy", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is False
        assert sorted(body["effective_methods"]) == ["env_key", "pat", "scim"]

        r = c.post(
            "/keys",
            json={"name": "globex-ci", "roles": ["writer"]},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text


def test_empty_methods_array_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/auth-methods-policy",
            json={"methods": []},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert "at least one" in r.text


def test_unknown_method_silently_dropped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/auth-methods-policy",
            json={"methods": ["env_key", "pat", "scim", "carrier_pigeon"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert sorted(r.json()["methods"]) == ["env_key", "pat", "scim"]
