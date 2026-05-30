from __future__ import annotations

import pytest
from clawhum_core import error_tracking as errors_mod
from clawhum_core.settings import get_settings


def _reset_settings():
    get_settings.cache_clear()


def test_init_returns_false_when_dsn_empty(monkeypatch):
    monkeypatch.delenv("CLAWHUM_SENTRY_DSN", raising=False)
    _reset_settings()
    assert errors_mod.init_error_tracking() is False


def test_init_returns_false_when_sdk_missing(monkeypatch):
    monkeypatch.setenv("CLAWHUM_SENTRY_DSN", "https://example@sentry.io/1")
    _reset_settings()
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("sentry_sdk"):
            raise ImportError("forced for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert errors_mod.init_error_tracking() is False


def test_scrub_removes_sensitive_headers():
    event = {
        "request": {
            "headers": {
                "x-api-key": "supersecret",
                "Authorization": "Bearer abc",
                "user-agent": "pytest",
            }
        }
    }
    cleaned = errors_mod._scrub(event, {})
    headers = cleaned["request"]["headers"]
    assert headers["x-api-key"] == "[Filtered]"
    assert headers["Authorization"] == "[Filtered]"
    assert headers["user-agent"] == "pytest"


def test_scrub_attaches_request_id_from_scope():
    event = {"tags": {}}
    hint = {"asgi_scope": {"state": {"request_id": "req-123"}}}
    cleaned = errors_mod._scrub(event, hint)
    assert cleaned["tags"]["request_id"] == "req-123"


def test_init_wires_sdk_when_configured(monkeypatch):
    sentry_sdk = pytest.importorskip("sentry_sdk")
    monkeypatch.setenv("CLAWHUM_SENTRY_DSN", "https://example@sentry.io/1")
    monkeypatch.setenv("CLAWHUM_SENTRY_ENVIRONMENT", "test")
    monkeypatch.setenv("CLAWHUM_SENTRY_TRACES_SAMPLE_RATE", "0.25")
    _reset_settings()

    captured: dict = {}
    orig_init = sentry_sdk.init

    def fake_init(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    try:
        assert errors_mod.init_error_tracking() is True
    finally:
        sentry_sdk.init = orig_init  # type: ignore[assignment]

    assert captured["dsn"] == "https://example@sentry.io/1"
    assert captured["environment"] == "test"
    assert captured["traces_sample_rate"] == pytest.approx(0.25)
    assert captured["send_default_pii"] is False
    assert callable(captured["before_send"])
    # release tag includes the package version.
    assert captured["release"].startswith("clawhum@")
