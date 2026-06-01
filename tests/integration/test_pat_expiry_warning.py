"""Per-workspace PAT expiry advance-warning header tests.

What an enterprise security review checks here:

1. With no policy, a PAT-authenticated response carries no Sunset or
   Deprecation header even when the PAT is days from expiry.
2. With a 30-day warning policy, a PAT that expires in 7 days returns
   Sunset, Deprecation: true, X-Clawhum-Token-Expires-In, and
   X-Clawhum-Token-Expires-At on every response, including reads.
3. A PAT that expires far outside the window (e.g. 365 days) returns
   none of those headers, so quiet days stay quiet.
4. Workspace A's policy is invisible to workspace B: B's PAT, even
   when close to expiry, gets no headers unless B opts in.
5. Malformed policy submissions are rejected with HTTP 400 and a
   structured error before they can land in the store.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_PAT_EXPIRY_WARNING_PATH",
        str(tmp_path / "pat_expiry_warning.jsonl"),
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
    from clawhum_api import pat_expiry_warning

    pat_expiry_warning.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _mint_pat(c: TestClient, key: str, *, expires_in_days: int) -> str:
    r = c.post(
        "/keys",
        json={
            "name": "ci",
            "roles": ["admin"],
            "expires_in_days": expires_in_days,
        },
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200, r.text
    return r.json()["secret"]


def test_no_policy_no_warning_headers(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        secret = _mint_pat(c, "acmekey", expires_in_days=1)
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text
        assert "Sunset" not in r.headers
        assert "Deprecation" not in r.headers
        assert "X-Clawhum-Token-Expires-In" not in r.headers


def test_policy_attaches_headers_inside_window(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-expiry-warning",
            json={"warn_within_days": 30, "docs_url": "https://example.test/rotate"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is True
        assert body["warn_within_days"] == 30
        # Example headers in the GET response should be populated when
        # the policy is on so the dashboard can preview them.
        assert "Sunset" in body["example_headers"]

        secret = _mint_pat(c, "acmekey", expires_in_days=7)
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text
        assert "Sunset" in r.headers
        assert r.headers.get("Deprecation") == "true"
        expires_in = int(r.headers["X-Clawhum-Token-Expires-In"])
        # 7 days in seconds with some scheduling slack.
        assert 7 * 86400 - 120 < expires_in <= 7 * 86400
        assert r.headers["X-Clawhum-Token-Expires-At"].endswith("Z")
        # Link header should point at the docs URL when configured.
        assert 'rel="sunset"' in r.headers.get("Link", "")
        assert "https://example.test/rotate" in r.headers.get("Link", "")


def test_policy_silent_outside_window(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/pat-expiry-warning",
            json={"warn_within_days": 3},
            headers={"X-API-Key": "acmekey"},
        )
        secret = _mint_pat(c, "acmekey", expires_in_days=30)
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200
        assert "Sunset" not in r.headers
        assert "X-Clawhum-Token-Expires-In" not in r.headers


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme opts in, Globex does not.
        c.put(
            "/pat-expiry-warning",
            json={"warn_within_days": 30},
            headers={"X-API-Key": "acmekey"},
        )
        # Globex mints a PAT inside what would be Acme's window;
        # Globex must not see warning headers because Globex has no
        # policy of its own.
        globex_secret = _mint_pat(c, "globexkey", expires_in_days=7)
        r = c.get("/me", headers={"X-API-Key": globex_secret})
        assert r.status_code == 200
        assert "Sunset" not in r.headers
        assert "Deprecation" not in r.headers
        # And Globex cannot see Acme's policy either.
        r = c.get("/pat-expiry-warning", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["enforcing"] is False


def test_invalid_policy_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/pat-expiry-warning",
            json={"warn_within_days": 9999},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 422  # pydantic ge/le catches this
        r = c.put(
            "/pat-expiry-warning",
            json={"warn_within_days": 30, "docs_url": "ftp://nope"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_pat_expiry_warning"
