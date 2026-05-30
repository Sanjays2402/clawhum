"""Tests for per-API-key rate limiting and multi-key auth."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str = "", api_key: str = "changeme",
            rpm: int = 120):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", api_key)
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", str(rpm))

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_registry_parses_multikey_spec(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_API_KEYS", "ops:sk_ops:600,partner:sk_p:30,bare:sk_bare")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "100")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import build_registry

    reg = build_registry(default_rpm=100)
    assert not reg.is_open()
    assert reg.lookup("sk_ops").name == "ops"
    assert reg.lookup("sk_ops").rpm == 600
    assert reg.lookup("sk_p").rpm == 30
    # Bare entry (no rpm) inherits default.
    assert reg.lookup("sk_bare").rpm == 100
    assert reg.lookup("nope") is None


def test_legacy_single_key_still_works(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, api_key="legacy-key", rpm=10) as c:
        bad = c.post("/feedback", json={}, headers={"x-api-key": "wrong"})
        assert bad.status_code == 401
        ok = c.post("/feedback", json={"track_id": "x", "match_id": "y", "vote": "up"},
                    headers={"x-api-key": "legacy-key"})
        # 200/201/400/422 acceptable; the point is it is NOT 401.
        assert ok.status_code != 401


def test_per_key_rate_limit_isolated(monkeypatch, tmp_path):
    # Two keys, fast key gets 50/min, slow key gets 2/min.
    spec = "fast:sk_fast:50,slow:sk_slow:2"
    with _client(monkeypatch, tmp_path, api_keys=spec, rpm=120) as c:
        # Burn through slow key budget on a public route (health is skipped,
        # so use /ready? also skipped). Use a routed GET like /metrics? also
        # skipped. The limiter only applies outside skip set, so hit /match
        # or any 404 path that is not skipped.
        r1 = c.get("/library/tracks", headers={"x-api-key": "sk_slow"})
        r2 = c.get("/library/tracks", headers={"x-api-key": "sk_slow"})
        r3 = c.get("/library/tracks", headers={"x-api-key": "sk_slow"})
        assert r1.status_code != 429
        assert r2.status_code != 429
        assert r3.status_code == 429
        assert r3.headers.get("Retry-After")
        assert r3.headers.get("X-RateLimit-Limit") == "2"

        # Fast key has its own bucket and is unaffected.
        rf = c.get("/library/tracks", headers={"x-api-key": "sk_fast"})
        assert rf.status_code != 429
        assert rf.headers.get("X-RateLimit-Limit") == "50"


def test_unknown_key_falls_back_to_ip_bucket(monkeypatch, tmp_path):
    # Default rpm tiny so we can prove the per-IP fallback path triggers.
    with _client(monkeypatch, tmp_path, api_keys="", api_key="changeme", rpm=2) as c:
        # No key configured (dev mode), all requests go to per-IP bucket.
        a = c.get("/library/tracks")
        b = c.get("/library/tracks")
        c3 = c.get("/library/tracks")
        assert a.status_code != 429
        assert b.status_code != 429
        assert c3.status_code == 429
        assert c3.headers.get("X-RateLimit-Limit") == "2"


def test_rate_limit_headers_present_on_success(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, api_keys="ops:sk_ops:50", rpm=120) as c:
        r = c.get("/library/tracks", headers={"x-api-key": "sk_ops"})
        assert r.headers.get("X-RateLimit-Limit") == "50"
        assert int(r.headers["X-RateLimit-Remaining"]) <= 49
