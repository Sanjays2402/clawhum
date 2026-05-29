from fastapi.testclient import TestClient


def test_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    with TestClient(create_app()) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "clawhum_uptime_seconds" in r.text
