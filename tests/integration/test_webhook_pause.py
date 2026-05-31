"""Pause/resume webhook deliveries without losing config or history.

Covers:
  * pause flips ``active`` to False so the dispatcher skips the hook
  * resume flips it back so deliveries fire again
  * member role (non-admin) cannot pause or resume (403)
  * cross-tenant pause is denied (404, not 403, to avoid id enumeration)
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH",
        str(tmp_path / "webhook_deliveries.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "main:adminkey:10000:admin,"
        "main:memberkey:10000:member,"
        "other:otherkey:10000:admin",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _create(c, key="adminkey"):
    r = c.post(
        "/webhooks",
        json={"url": "https://example.com/hook", "events": ["match.completed"]},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_pause_and_resume_round_trip(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        hook_id = _create(c)

        # Pause: active should flip to False, paused_at populated.
        r = c.post(
            f"/webhooks/{hook_id}/pause",
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == hook_id
        assert body["active"] is False
        assert body["paused_at"] is not None

        # List reflects paused state.
        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        hook = next(h for h in listing["webhooks"] if h["id"] == hook_id)
        assert hook["active"] is False
        assert hook["paused_at"] is not None

        # Dispatch must skip paused hooks: no delivery is recorded.
        from clawhum_api.routes.webhooks import dispatch_event

        sent = asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))
        assert sent == 0  # paused hook should not receive the event
        deliveries = c.get(
            f"/webhooks/{hook_id}/deliveries",
            headers={"X-API-Key": "adminkey"},
        ).json()
        assert deliveries["deliveries"] == []

        # Resume: active flips back to True.
        r = c.post(
            f"/webhooks/{hook_id}/resume",
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is True
        assert body["resumed_at"] is not None

        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        hook = next(h for h in listing["webhooks"] if h["id"] == hook_id)
        assert hook["active"] is True


def test_pause_requires_admin_role(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        hook_id = _create(c)
        r = c.post(
            f"/webhooks/{hook_id}/pause",
            headers={"X-API-Key": "memberkey"},
        )
        assert r.status_code == 403, r.text


def test_pause_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        hook_id = _create(c, key="adminkey")  # owned by 'main'
        # 'other' admin tries to pause main's hook
        r = c.post(
            f"/webhooks/{hook_id}/pause",
            headers={"X-API-Key": "otherkey"},
        )
        assert r.status_code == 404, r.text

        # And main's hook is still active.
        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        assert listing["webhooks"][0]["active"] is True
