"""Per-workspace PAT minimum-requirements enforcement tests.

What an enterprise security review checks here:

1. With no policy, mint behaviour is unchanged (opt-in feature).
2. With ``require_owner_email``, a mint missing ``owner_email`` is
   rejected at /keys with HTTP 400 and a machine-parseable error
   code naming the violation, while a compliant mint succeeds.
3. With ``require_expiry`` and ``max_expiry_days``, a mint with no
   or too-long expiry is rejected; a mint inside the cap succeeds.
4. With ``require_ip_cidrs``, a mint with empty ip_cidrs is
   rejected; a mint with one CIDR succeeds.
5. Tenant A's policy is invisible to tenant B and does not block
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
        "CLAWHUM_PAT_MIN_REQUIREMENTS_PATH",
        str(tmp_path / "pat_min_requirements.jsonl"),
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
    from clawhum_api import pat_min_requirements

    pat_min_requirements.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_no_policy_means_no_restriction(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get(
            "/pat-min-requirements", headers={"X-API-Key": "acmekey"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is False

        r = c.post(
            "/keys",
            json={"name": "bare", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_require_owner_email_blocks_anonymous_mint(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-min-requirements",
            json={
                "require_owner_email": True,
                "require_expiry": False,
                "max_expiry_days": 0,
                "require_ip_cidrs": False,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is True

        r = c.post(
            "/keys",
            json={"name": "anon", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "pat_min_requirements_violation"
        assert "owner_email_required" in detail["violations"]

        r = c.post(
            "/keys",
            json={
                "name": "owned",
                "roles": ["writer"],
                "owner_email": "ops@acme.test",
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_require_expiry_with_cap(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-min-requirements",
            json={
                "require_owner_email": False,
                "require_expiry": True,
                "max_expiry_days": 30,
                "require_ip_cidrs": False,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # No expiry: rejected.
        r = c.post(
            "/keys",
            json={"name": "no-exp", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert "expiry_required" in r.json()["detail"]["violations"]

        # Too long: rejected.
        r = c.post(
            "/keys",
            json={
                "name": "too-long",
                "roles": ["writer"],
                "expires_in_days": 365,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert any(
            v.startswith("expiry_exceeds_max")
            for v in r.json()["detail"]["violations"]
        )

        # Inside the cap: accepted.
        r = c.post(
            "/keys",
            json={
                "name": "ok",
                "roles": ["writer"],
                "expires_in_days": 14,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_require_ip_cidrs_blocks_unscoped_mint(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-min-requirements",
            json={
                "require_owner_email": False,
                "require_expiry": False,
                "max_expiry_days": 0,
                "require_ip_cidrs": True,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        r = c.post(
            "/keys",
            json={"name": "unscoped", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert "ip_cidrs_required" in r.json()["detail"]["violations"]

        r = c.post(
            "/keys",
            json={
                "name": "scoped",
                "roles": ["writer"],
                "ip_cidrs": ["10.0.0.0/8"],
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme pins owner_email required.
        r = c.put(
            "/pat-min-requirements",
            json={
                "require_owner_email": True,
                "require_expiry": False,
                "max_expiry_days": 0,
                "require_ip_cidrs": False,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Globex reads its own policy and sees no enforcement.
        r = c.get(
            "/pat-min-requirements", headers={"X-API-Key": "globexkey"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is False

        # Globex can mint without owner_email even though Acme cannot.
        r = c.post(
            "/keys",
            json={"name": "globex-bare", "roles": ["writer"]},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text

        # Acme still blocked.
        r = c.post(
            "/keys",
            json={"name": "acme-bare", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
