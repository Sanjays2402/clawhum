"""Verify PAT fine-grained scopes deny least-privilege violations.

A PAT minted with only ``read:library`` must not be able to call a
``read:matches`` route, even though the underlying role would normally
permit both. A PAT minted with no scopes (legacy or full-trust) must
keep working unchanged. The scope ceiling is also enforced at mint
time: a writer-role caller cannot stuff ``admin`` into the body and
walk away with an admin-scoped token.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:writer:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _mint(c, secret_header, body):
    r = c.post("/keys", json=body, headers={"X-API-Key": secret_header})
    assert r.status_code == 200, r.text
    return r.json()


def test_scope_clamped_at_mint_to_caller_role(monkeypatch, tmp_path):
    """A writer cannot mint a PAT carrying the ``admin`` scope."""
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(
            c,
            "opskey",
            {"name": "narrow", "scopes": ["read:library", "admin", "garbage"]},
        )
        # admin clamped away; unknown dropped; legitimate scope kept.
        assert "admin" not in view["scopes"]
        assert "garbage" not in view["scopes"]
        assert view["scopes"] == ["read:library"]
        # effective scopes reported back for the UI.
        assert view["effective_scopes"] == ["read:library"]


def test_scope_denies_out_of_scope_route(monkeypatch, tmp_path):
    """A PAT scoped to read:library must get 403 on /stats? no — on /match."""
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(
            c,
            "opskey",
            {"name": "lib-only", "scopes": ["read:library"]},
        )
        pat_secret = view["secret"]

        # Allowed: /stats requires read:library.
        r = c.get("/stats", headers={"X-API-Key": pat_secret})
        assert r.status_code == 200, r.text

        # Denied: /reindex requires write:library.
        r = c.post(
            "/reindex",
            json={"library_path": None, "spotify_playlist": None, "use_clap": False},
            headers={"X-API-Key": pat_secret},
        )
        assert r.status_code == 403, r.text
        assert "write:library" in r.text


def test_no_scopes_means_role_default(monkeypatch, tmp_path):
    """A PAT minted with no scopes keeps every scope the role allows."""
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(c, "opskey", {"name": "legacy"})
        pat_secret = view["secret"]
        # No explicit scopes stored.
        assert view["scopes"] == []
        # But effective scopes include both writer-level scopes.
        assert "read:library" in view["effective_scopes"]
        assert "write:library" in view["effective_scopes"]

        # And the token works on a read:library route.
        r = c.get("/stats", headers={"X-API-Key": pat_secret})
        assert r.status_code == 200
