"""Per-workspace PAT secret prefix policy enforcement tests.

What an enterprise security review checks here:

1. With no policy, PAT secrets keep the global ``pat_`` shape.
2. With a policy, every NEW PAT secret is shaped
   ``pat_<workspace_prefix>_<random>`` so a workspace-scoped
   credential scanner can attribute leaks to the right tenant.
3. Rotation also honours the workspace prefix.
4. Tenant A's prefix policy is invisible to tenant B and does not
   affect tenant B's mint shape (no cross-tenant leakage).
5. Malformed prefixes are rejected with HTTP 400 and a structured
   error before they can land in the store.
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
        "CLAWHUM_PAT_SECRET_PREFIX_PATH",
        str(tmp_path / "pat_secret_prefix.jsonl"),
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
    from clawhum_api import pat_secret_prefix

    pat_secret_prefix.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_no_policy_uses_global_pat_prefix(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/pat-secret-prefix", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is False
        assert body["prefix"] == ""

        r = c.post(
            "/keys",
            json={"name": "ci", "roles": ["admin"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]
        assert secret.startswith("pat_")
        # No tenant infix means the body starts immediately after pat_,
        # which is base64url so could itself contain underscores; we
        # only assert the legacy shape did not gain a tenant segment.
        assert not secret[4:].startswith("acme_")


def test_policy_shapes_new_mints(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-secret-prefix",
            json={"prefix": "acme"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is True
        assert body["prefix"] == "acme"
        assert body["scanner_regex"].startswith("pat_acme_")

        r = c.post(
            "/keys",
            json={"name": "shaped", "roles": ["admin"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]
        assert secret.startswith("pat_acme_"), secret


def test_rotation_honours_workspace_prefix(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/pat-secret-prefix",
            json={"prefix": "acme"},
            headers={"X-API-Key": "acmekey"},
        )
        r = c.post(
            "/keys",
            json={"name": "rot", "roles": ["admin"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        pat_id = r.json()["id"]

        r = c.post(
            f"/keys/{pat_id}/rotate",
            json={"grace_minutes": 0},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        rotated_secret = r.json()["secret"]
        assert rotated_secret.startswith("pat_acme_"), rotated_secret


def test_invalid_prefix_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        for bad in ["A", "acme_co", "-acme", "acme-", "a" * 17, " ", "acme!"]:
            r = c.put(
                "/pat-secret-prefix",
                json={"prefix": bad},
                headers={"X-API-Key": "acmekey"},
            )
            # An all-whitespace string normalises to "", which is a
            # legal "clear policy" call, so accept either 400 or
            # 200-with-empty-prefix for that one input.
            if bad.strip() == "":
                assert r.status_code == 200, (bad, r.text)
                assert r.json()["prefix"] == ""
                continue
            assert r.status_code == 400, (bad, r.text)
            detail = r.json()["detail"]
            assert detail["error"] == "invalid_pat_prefix"


def test_no_cross_tenant_leakage(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/pat-secret-prefix",
            json={"prefix": "acme"},
            headers={"X-API-Key": "acmekey"},
        )
        # Globex must not see acme's prefix
        r = c.get(
            "/pat-secret-prefix", headers={"X-API-Key": "globexkey"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["prefix"] == ""

        # And globex mints must NOT carry the acme prefix
        r = c.post(
            "/keys",
            json={"name": "g", "roles": ["admin"]},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]
        assert not secret.startswith("pat_acme_"), secret
        # And globex secrets keep the legacy shape with no tenant infix
        assert not secret[4:].startswith("acme_")


def test_clear_policy_returns_to_global(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/pat-secret-prefix",
            json={"prefix": "acme"},
            headers={"X-API-Key": "acmekey"},
        )
        r = c.put(
            "/pat-secret-prefix",
            json={"prefix": ""},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is False

        r = c.post(
            "/keys",
            json={"name": "after", "roles": ["admin"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]
        assert secret.startswith("pat_")
        # Either way, this token must not carry the cleared workspace prefix
        assert not secret.startswith("pat_acme_")
