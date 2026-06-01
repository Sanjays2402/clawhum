"""Per-workspace PAT concurrency cap enforcement tests.

What an enterprise security review checks here:

1. With no cap, mint behaviour is unchanged (opt-in feature).
2. With a cap, the (N+1)-th live PAT mint is rejected at /keys
   with HTTP 429 and a machine-parseable error code that names
   the live count and the cap.
3. Revoking a token frees a slot so the next mint succeeds.
4. Tenant A's cap is invisible to tenant B and does not block
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
        "CLAWHUM_PAT_CONCURRENCY_PATH", str(tmp_path / "pat_concurrency.jsonl")
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
    from clawhum_api import pat_concurrency

    pat_concurrency.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_no_cap_means_no_restriction(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/pat-concurrency", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is False
        assert body["max_active"] == 0

        for i in range(3):
            r = c.post(
                "/keys",
                json={"name": f"ci-{i}", "roles": ["writer"]},
                headers={"X-API-Key": "acmekey"},
            )
            assert r.status_code == 200, r.text


def test_cap_blocks_extra_mints_with_429(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-concurrency",
            json={"max_active": 2},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is True
        assert r.json()["max_active"] == 2

        # First two mints succeed
        ids = []
        for i in range(2):
            r = c.post(
                "/keys",
                json={"name": f"ci-{i}", "roles": ["writer"]},
                headers={"X-API-Key": "acmekey"},
            )
            assert r.status_code == 200, r.text
            ids.append(r.json()["id"])

        # Third mint is rejected
        r = c.post(
            "/keys",
            json={"name": "ci-overflow", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 429, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "pat_concurrency_exceeded"
        assert detail["max_active"] == 2
        assert detail["live"] >= 2

        # Revoking one frees a slot
        rev = c.delete(f"/keys/{ids[0]}", headers={"X-API-Key": "acmekey"})
        assert rev.status_code == 200, rev.text
        r = c.post(
            "/keys",
            json={"name": "ci-recover", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_cap_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme pins a cap of 1
        r = c.put(
            "/pat-concurrency",
            json={"max_active": 1},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Globex sees no cap and can mint freely
        r = c.get("/pat-concurrency", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        assert r.json()["enforcing"] is False

        for i in range(3):
            r = c.post(
                "/keys",
                json={"name": f"gx-{i}", "roles": ["writer"]},
                headers={"X-API-Key": "globexkey"},
            )
            assert r.status_code == 200, r.text

        # Acme is still capped at 1 regardless of Globex activity
        r = c.post(
            "/keys",
            json={"name": "acme-1", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        r = c.post(
            "/keys",
            json={"name": "acme-2", "roles": ["writer"]},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error"] == "pat_concurrency_exceeded"
