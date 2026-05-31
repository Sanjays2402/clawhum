"""Circuit breaker that auto disables a webhook after consecutive failures.

Covers:
  * N consecutive failed deliveries flip ``active`` to False and stamp
    ``auto_disabled_at`` / ``auto_disabled_reason``.
  * Dispatch then skips the broken hook so it stops burning retry budget.
  * An admin resume clears the breaker so a fresh streak is needed before
    auto disable trips again.
  * The auto disable event is recorded in the tamper evident audit log.
  * Cross tenant isolation: a storm on tenant A leaves tenant B untouched.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, threshold: int = 3):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH",
        str(tmp_path / "webhook_deliveries.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_AUTO_DISABLE_THRESHOLD", str(threshold)
    )
    monkeypatch.setenv("CLAWHUM_WEBHOOK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "main:adminkey:10000:admin,other:otherkey:10000:admin",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _create(c, key="adminkey"):
    r = c.post(
        "/webhooks",
        json={
            "url": "https://example.com/hook",
            "events": ["match.completed"],
        },
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _force_fail(monkeypatch):
    """Make every outbound POST a transport failure deterministically."""

    async def _boom(client, url, body, headers, timeout):
        return 0, "connection refused"

    from clawhum_api.routes import webhooks as wh

    monkeypatch.setattr(wh, "_post_once", _boom)


def test_auto_disable_after_threshold(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, threshold=3) as c:
        hook_id = _create(c)
        _force_fail(monkeypatch)

        from clawhum_api.routes.webhooks import dispatch_event, _live_hooks

        # Two failed dispatches should NOT yet disable the hook.
        for _ in range(2):
            asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))

        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        hook = next(h for h in listing["webhooks"] if h["id"] == hook_id)
        assert hook["active"] is True
        assert hook["auto_disabled_at"] is None
        assert hook["consecutive_failures"] == 2

        # Third failure trips the breaker.
        asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))
        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        hook = next(h for h in listing["webhooks"] if h["id"] == hook_id)
        assert hook["active"] is False, "circuit breaker should have tripped"
        assert hook["auto_disabled_at"] is not None
        assert "consecutive" in (hook["auto_disabled_reason"] or "")
        assert hook["consecutive_failures"] >= 3

        # Dispatcher must now skip the disabled hook: no new delivery rows.
        from clawhum_api.routes.webhooks import _deliveries_path

        before = sum(1 for _ in open(_deliveries_path()))
        sent = asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))
        after = sum(1 for _ in open(_deliveries_path()))
        assert sent == 0
        assert after == before

        # Confirm the live hook reducer also sees it as inactive.
        live = {h["id"]: h for h in _live_hooks("main")}
        assert live[hook_id]["active"] is False
        assert live[hook_id]["auto_disabled_at"] is not None

        # The auto disable should be recorded in the audit log.
        audit_lines = list(open(tmp_path / "audit.jsonl"))
        assert any(
            "webhook.auto_disabled" in line and hook_id in line
            for line in audit_lines
        ), "auto disable must hit the audit log"


def test_resume_resets_breaker_budget(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, threshold=2) as c:
        hook_id = _create(c)
        _force_fail(monkeypatch)

        from clawhum_api.routes.webhooks import dispatch_event

        # Trip the breaker.
        for _ in range(2):
            asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))
        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        assert next(
            h for h in listing["webhooks"] if h["id"] == hook_id
        )["active"] is False

        # Admin resume: hook flips back, counter should restart from zero.
        r = c.post(
            f"/webhooks/{hook_id}/resume",
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text

        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        hook = next(h for h in listing["webhooks"] if h["id"] == hook_id)
        assert hook["active"] is True
        # Past failures predate the resume boundary and must not count.
        assert hook["consecutive_failures"] == 0
        assert hook["auto_disabled_at"] is None

        # A single fresh failure must not re-disable (threshold is 2 again).
        asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))
        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        hook = next(h for h in listing["webhooks"] if h["id"] == hook_id)
        assert hook["active"] is True
        assert hook["consecutive_failures"] == 1


def test_auto_disable_is_tenant_scoped(monkeypatch, tmp_path):
    """A storm of failures on tenant A must not touch tenant B's hook."""
    with _client(monkeypatch, tmp_path, threshold=2) as c:
        hook_a = _create(c, key="adminkey")
        hook_b = _create(c, key="otherkey")
        _force_fail(monkeypatch)

        from clawhum_api.routes.webhooks import dispatch_event

        for _ in range(3):
            asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))

        listing_a = c.get(
            "/webhooks", headers={"X-API-Key": "adminkey"}
        ).json()
        listing_b = c.get(
            "/webhooks", headers={"X-API-Key": "otherkey"}
        ).json()
        ha = next(h for h in listing_a["webhooks"] if h["id"] == hook_a)
        hb = next(h for h in listing_b["webhooks"] if h["id"] == hook_b)
        assert ha["active"] is False
        assert hb["active"] is True
        assert hb["consecutive_failures"] == 0
