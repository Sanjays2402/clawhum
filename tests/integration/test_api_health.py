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


def test_ready(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/ready").status_code == 200
