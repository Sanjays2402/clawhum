from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_health(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_live_always_ok(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/live")
        assert r.status_code == 200
        assert r.json() == {"live": True}


def test_ready_after_boot(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/ready")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ready"] is True
        # Real checks are reported, not a placeholder true.
        assert "boot" in body["checks"]
        assert body["checks"]["boot"] == "ok"
        assert body["checks"]["embedder"].startswith("ok:")
        assert body["checks"]["index"].startswith("ok:")
        assert body["checks"]["auth"].startswith("ok:")


def test_ready_503_before_lifespan(monkeypatch, tmp_path):
    # Without entering the context manager, lifespan startup does not run
    # so app.state.clawhum is unset. Readiness must return 503 so the
    # pod is held out of the Service endpoints during boot.
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    c = TestClient(create_app())
    r = c.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert body["checks"]["boot"].startswith("fail")
    r2 = c.get("/startup")
    assert r2.status_code == 503
    assert r2.json()["started"] is False


def test_startup_endpoint(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/startup")
        assert r.status_code == 200
        assert r.json()["started"] is True
