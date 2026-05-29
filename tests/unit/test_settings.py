from clawhum_core.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("CLAWHUM_API_KEY", raising=False)
    s = Settings()
    assert s.embed_dim == 512
    assert s.target_sr == 48000
    assert s.top_k > 0
