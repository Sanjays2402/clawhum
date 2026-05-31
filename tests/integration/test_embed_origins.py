"""Tests for the per-workspace embed origin allowlist.

These three cases cover what an enterprise security review actually
checks:

1. Without rules, behaviour is unchanged (opt-in feature).
2. With rules, ``GET /share/{id}`` rejects a non-allowed browser
   origin with 403, and the share response advertises the allowlist.
3. Origins from tenant A are invisible to tenant B, and do not affect
   tenant B's shares (no cross-tenant leakage at the query layer).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_EMBED_ORIGINS_PATH", str(tmp_path / "eo.jsonl"))
    monkeypatch.setenv("CLAWHUM_SHARES_PATH", str(tmp_path / "shares.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import embed_origins
    embed_origins.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _make_share(c: TestClient, key: str) -> str:
    payload = {
        "query_id": "q1",
        "elapsed_ms": 12,
        "count": 1,
        "results": [{"track_id": "t1", "title": "Hum", "artist": "Anon", "score": 0.99}],
    }
    r = c.post("/share", json=payload, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_no_rules_no_restriction(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    sid = _make_share(c, "sk_admin")
    # Browser request with a random origin still works when no allowlist.
    r = c.get(f"/share/{sid}", headers={"Origin": "https://random.example"})
    assert r.status_code == 200
    body = r.json()
    assert body["embed_allowed_origins"] == []


def test_disallowed_origin_blocked(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    sid = _make_share(c, "sk_admin")
    # Register one allowed origin.
    r = c.post(
        "/embed-origins",
        json={"origin": "https://docs.acme.com", "label": "docs"},
        headers={"X-API-Key": "sk_admin"},
    )
    assert r.status_code == 201, r.text

    # Allowed origin still works and the share advertises the allowlist.
    ok = c.get(f"/share/{sid}", headers={"Origin": "https://docs.acme.com"})
    assert ok.status_code == 200
    assert ok.json()["embed_allowed_origins"] == ["https://docs.acme.com"]

    # Hostile origin gets 403.
    bad = c.get(f"/share/{sid}", headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403
    assert "origin" in bad.json()["detail"].lower()

    # Server-to-server (no Origin header) is still allowed: this
    # preserves crawler / link-preview behaviour for public shares.
    s2s = c.get(f"/share/{sid}")
    assert s2s.status_code == 200


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    spec = "acme_ops:sk_acme:9999:admin:acme,globex_ops:sk_globex:9999:admin:globex"
    c = _client(monkeypatch, tmp_path, spec)

    # Acme locks its embed origin list to one host.
    r = c.post(
        "/embed-origins",
        json={"origin": "https://docs.acme.com"},
        headers={"X-API-Key": "sk_acme"},
    )
    assert r.status_code == 201

    # Globex cannot see acme's origins.
    g_list = c.get("/embed-origins", headers={"X-API-Key": "sk_globex"})
    assert g_list.status_code == 200
    assert g_list.json() == {"enforcing": False, "origins": []}

    # Globex creates a share. Acme's allowlist must not gate it.
    gsid = _make_share(c, "sk_globex")
    g_view = c.get(f"/share/{gsid}", headers={"Origin": "https://anything.example"})
    assert g_view.status_code == 200
    assert g_view.json()["embed_allowed_origins"] == []

    # Cannot delete a rule belonging to another tenant.
    acme_origin_id = r.json()["id"]
    del_bad = c.delete(f"/embed-origins/{acme_origin_id}", headers={"X-API-Key": "sk_globex"})
    assert del_bad.status_code == 404


def test_invalid_origin_rejected(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    for bad in ("not a url", "ftp://x.com", "https:///nohost", "https://x.com/path"):
        r = c.post("/embed-origins", json={"origin": bad}, headers={"X-API-Key": "sk_admin"})
        # The last case has a path; we accept by stripping, so it should normalize.
        if bad == "https://x.com/path":
            assert r.status_code == 201
            assert r.json()["origin"] == "https://x.com"
        else:
            assert r.status_code == 400, f"expected 400 for {bad}, got {r.status_code}"
