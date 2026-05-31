"""Tests for /usage endpoint and the usage recorder."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str = "") -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    if api_keys:
        monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
        monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    else:
        monkeypatch.delenv("CLAWHUM_API_KEY", raising=False)
        monkeypatch.delenv("CLAWHUM_API_KEYS", raising=False)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "120")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_usage_endpoint_returns_zero_when_no_events(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/usage")
        assert r.status_code == 200
        body = r.json()
        assert body["month"]["total"] == 0
        assert body["month"]["remaining"] == body["quota_per_month"]
        assert len(body["daily_buckets"]) == 30
        # /usage itself is a GET, not chargeable, so it should stay at zero.
        assert body["day"]["total"] == 0


def test_recorder_aggregates_chargeable_events(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api import usage as usage_mod

    now = time.time()
    usage_mod.record_event("acme", "match", ts=now)
    usage_mod.record_event("acme", "batch", ts=now - 100)
    usage_mod.record_event("acme", "match", ts=now - 86_400 * 2)
    # Different tenant, should not bleed into acme totals.
    usage_mod.record_event("other", "match", ts=now)

    counts = usage_mod.recent_counts("acme", now=now)
    assert counts["minute"]["total"] == 1
    assert counts["day"]["total"] == 2
    assert counts["month"]["total"] == 3
    assert counts["month"]["by_event"]["match"] == 2
    assert counts["month"]["by_event"]["batch"] == 1
    # Today's bucket is index 29 (newest).
    assert counts["daily_buckets"][29] >= 1
