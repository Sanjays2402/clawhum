"""Per-PAT HTTP method allowlist enforcement.

These tests prove the per-credential method fence is real: a token
minted with method_allowlist=['GET'] is accepted on GET routes and
rejected with HTTP 405 on POST/PUT/DELETE, HEAD is implicitly allowed
when GET is (so monitoring probes stay green), bad input returns a
structured 400, clearing the list restores unrestricted access, and
cross-tenant attempts to mutate the policy return 404 rather than
leaking the existence of someone else's token.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:opskey:10000:writer:acme,umbrella:umbkey:10000:writer:umbrella",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_method_allowlist_blocks_writes_and_allows_reads(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "readonly-bot", "http_methods": ["GET"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["http_methods"] == ["GET"]
        secret = body["secret"]

        # GET on an authed route succeeds.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text

        # HEAD is implicitly allowed by the helper when GET is in the
        # allowlist; verified via the unit-level helper since not
        # every GET route in FastAPI registers a HEAD handler.
        from clawhum_api import pat_store as _ps
        assert _ps.method_matches_allowlist("HEAD", ["GET"]) is True
        assert _ps.method_matches_allowlist("POST", ["GET"]) is False

        # POST on an authed route is rejected with 405 and Allow header.
        r = c.post(
            "/feedback",
            json={"query_id": "q", "match_id": "m", "rating": 1},
            headers={"X-API-Key": secret},
        )
        assert r.status_code == 405, r.text
        assert "not in pat allowlist" in r.json().get("detail", "")
        allow = r.headers.get("Allow", "")
        assert "GET" in allow and "HEAD" in allow
        assert r.headers.get("X-Pat-Method-Denied") == "POST"


def test_method_allowlist_validates_and_clears(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/keys", json={"name": "ops"}, headers={"X-API-Key": "opskey"})
        assert r.status_code == 200, r.text
        pat_id = r.json()["id"]
        secret = r.json()["secret"]
        assert r.json()["http_methods"] == []

        # Unrestricted by default: POST reachable.
        r = c.post(
            "/feedback",
            json={"query_id": "q", "match_id": "m", "rating": 1},
            headers={"X-API-Key": secret},
        )
        assert r.status_code in (200, 201, 422), r.text

        # Tighten to GET only.
        r = c.put(
            f"/keys/{pat_id}/method-allowlist",
            json={"http_methods": ["get"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["http_methods"] == ["GET"]

        # Now POST is blocked.
        r = c.post(
            "/feedback",
            json={"query_id": "q", "match_id": "m", "rating": 1},
            headers={"X-API-Key": secret},
        )
        assert r.status_code == 405

        # Unknown verb returns 400, not 500.
        r = c.put(
            f"/keys/{pat_id}/method-allowlist",
            json={"http_methods": ["GETS"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 400

        # Clearing restores access.
        r = c.put(
            f"/keys/{pat_id}/method-allowlist",
            json={"http_methods": []},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200
        assert r.json()["http_methods"] == []
        r = c.post(
            "/feedback",
            json={"query_id": "q", "match_id": "m", "rating": 1},
            headers={"X-API-Key": secret},
        )
        assert r.status_code in (200, 201, 422)


def test_method_allowlist_is_tenant_isolated(monkeypatch, tmp_path):
    """Umbrella admin cannot read or rewrite an Acme PAT's method fence."""
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "acme-bot", "http_methods": ["GET"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        acme_pat_id = r.json()["id"]

        # Umbrella tries to widen Acme's token to allow writes.
        r = c.put(
            f"/keys/{acme_pat_id}/method-allowlist",
            json={"http_methods": ["GET", "POST", "DELETE"]},
            headers={"X-API-Key": "umbkey"},
        )
        # Must be 404 (not 403 with detail) so cross-tenant existence
        # is not leaked.
        assert r.status_code == 404, r.text

        # Acme's PAT is unchanged.
        r = c.get("/keys", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        found = [k for k in r.json() if k["id"] == acme_pat_id]
        assert found and found[0]["http_methods"] == ["GET"]
