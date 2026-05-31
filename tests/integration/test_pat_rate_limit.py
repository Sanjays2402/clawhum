"""Per-PAT rate limit enforcement.

Confirms that a PAT minted with `rpm=N` actually gets a 429 after N
requests within the same minute, independent of the ambient API key
ceiling and independent of other PATs in the same workspace.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "acme:opskey:10000:writer:acme")
    # Loose ambient ceiling so per-PAT limit is the binding constraint.
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _mint_pat(client, name: str, rpm: int) -> str:
    r = client.post(
        "/keys",
        json={"name": name, "rpm": rpm},
        headers={"X-API-Key": "opskey"},
    )
    assert r.status_code == 200, r.text
    return r.json()["secret"]


def test_pat_rpm_enforced_per_token(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        tight = _mint_pat(c, "tight", rpm=3)
        loose = _mint_pat(c, "loose", rpm=50)

        ok = 0
        last_status = 200
        last_headers: dict[str, str] = {}
        for _ in range(6):
            r = c.get("/me", headers={"X-API-Key": tight})
            last_status = r.status_code
            last_headers = dict(r.headers)
            if r.status_code == 200:
                ok += 1
            else:
                break

        assert ok == 3, f"expected 3 successful calls before 429, got {ok}"
        assert last_status == 429
        assert last_headers.get("retry-after")
        assert last_headers.get("x-ratelimit-limit") == "3"
        assert last_headers.get("x-ratelimit-remaining") == "0"

        # Sibling PAT in the same workspace must still work: per-PAT
        # bucket isolation, not a shared workspace bucket at this layer.
        r = c.get("/me", headers={"X-API-Key": loose})
        assert r.status_code == 200, r.text
        # And it advertises its own (larger) ceiling.
        assert r.headers.get("x-ratelimit-limit") == "50"


def test_pat_rpm_zero_falls_back_to_default(monkeypatch, tmp_path):
    """A PAT minted with rpm=0 (unset) inherits the global default, not 0."""
    with _client(monkeypatch, tmp_path) as c:
        secret = _mint_pat(c, "default", rpm=0)
        # First call succeeds and reports the ambient default ceiling
        # rather than "0 means block everything". This is the same
        # behaviour the legacy API-key path has long had.
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200
        assert r.headers.get("x-ratelimit-limit") == "10000"
