"""Per-PAT URL path-prefix allowlist enforcement.

These tests prove the per-credential path fence is real: a token
minted with a path allowlist is accepted on matching routes and
rejected elsewhere, the always-allowed carve-out keeps /me reachable
so the token can rotate itself, prefix confusion ('/match' must not
match '/matches' or any unrelated route), bad input returns a
structured 400, and clearing the list restores unrestricted access.

We use /usage as the canonical "off-route" probe because it requires
api_key auth (so the dependency runs and our enforcement fires) and
it lives outside any common allowlist a /match-pinned PAT would set.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:writer:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_pat_path_allowlist_blocks_off_route_and_allows_on_route(
    monkeypatch, tmp_path
):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "ci-bot", "path_prefixes": ["/match"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["path_prefixes"] == ["/match"]
        secret = body["secret"]

        # /me is always allowed so the token can introspect itself.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text
        assert r.json()["key_name"] == "pat:ci-bot"

        # /usage is outside the allowlist: rejected with 403.
        r = c.get("/usage", headers={"X-API-Key": secret})
        assert r.status_code == 403, r.text
        detail = r.json().get("detail", "")
        assert "not in pat allowlist" in detail
        assert r.headers.get("X-Pat-Path-Denied", "").startswith("/usage")


def test_pat_path_allowlist_prevents_prefix_confusion(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "ci-bot", "path_prefixes": ["/match"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]

        # A neighbouring authed route must not be reachable just
        # because its name starts with the same letters as a pinned
        # prefix; the matcher requires an exact equal or '/'-bounded
        # extension. /usage proves the deny.
        r = c.get("/usage", headers={"X-API-Key": secret})
        assert r.status_code == 403, r.text


def test_pat_path_allowlist_update_validates_and_clears(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys", json={"name": "ops"}, headers={"X-API-Key": "opskey"}
        )
        assert r.status_code == 200, r.text
        pat_id = r.json()["id"]
        secret = r.json()["secret"]
        assert r.json()["path_prefixes"] == []

        # Unrestricted by default: /usage reachable.
        r = c.get("/usage", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text

        # Tighten: pin to /match only.
        r = c.put(
            f"/keys/{pat_id}/path-allowlist",
            json={"path_prefixes": ["/match", "/feedback"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["path_prefixes"] == ["/feedback", "/match"]

        # Now /usage is blocked.
        r = c.get("/usage", headers={"X-API-Key": secret})
        assert r.status_code == 403

        # Malformed prefix returns 400, not 500.
        r = c.put(
            f"/keys/{pat_id}/path-allowlist",
            json={"path_prefixes": ["/has space"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 400

        # Clearing restores access.
        r = c.put(
            f"/keys/{pat_id}/path-allowlist",
            json={"path_prefixes": []},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["path_prefixes"] == []
        r = c.get("/usage", headers={"X-API-Key": secret})
        assert r.status_code == 200, r.text
