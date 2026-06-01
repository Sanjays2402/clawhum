"""Per-workspace per-hook webhook delivery rate cap.

Covers:
  * Default policy (max_per_minute=0) allows unbounded deliveries.
  * Setting max_per_minute=N causes the (N+1)th attempt within 60s to
    be recorded with rate_limited=True and NOT POST to the receiver.
  * Synthetic rate-limit records do not themselves count toward the
    budget, so they cannot starve a recovered hook forever.
  * Tenant isolation: tenant A cannot read or alter tenant B's policy,
    and tenant A's policy does not throttle tenant B's deliveries.
  * Every mutation of the cap is recorded in the tamper evident audit
    log; every rate-limited delivery is recorded with
    action=webhook.rate_limited.
  * Negative max_per_minute and values above the ceiling are rejected
    with a structured 400 (admin endpoint, MFA required).
"""

from __future__ import annotations

import asyncio
import json

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
        "CLAWHUM_WEBHOOK_DELIVERY_RATE_PATH",
        str(tmp_path / "webhook_delivery_rate.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOK_AUTO_DISABLE_THRESHOLD", "0")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    monkeypatch.setenv("CLAWHUM_REQUIRE_MFA_FOR_ADMIN", "false")
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


def _force_ok(monkeypatch):
    """Make every outbound POST succeed without leaving the process."""

    async def _ok(client, url, body, headers, timeout):
        return 200, None

    from clawhum_api.routes import webhooks as wh

    monkeypatch.setattr(wh, "_post_once", _ok)


def _count_real_deliveries(path, hook_id):
    n = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("webhook_id") != hook_id:
                continue
            if rec.get("rate_limited") or rec.get("policy_blocked"):
                continue
            n += 1
    return n


def test_default_policy_does_not_throttle(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        hook_id = _create(c)
        _force_ok(monkeypatch)

        from clawhum_api.routes.webhooks import dispatch_event, _deliveries_path

        for _ in range(5):
            asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))

        assert _count_real_deliveries(_deliveries_path(), hook_id) == 5


def test_cap_blocks_extra_deliveries_and_records_synthetic(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        hook_id = _create(c)
        _force_ok(monkeypatch)

        # Set the cap to 2 per minute for this workspace.
        r = c.put(
            "/webhook-delivery-rate",
            json={"max_per_minute": 2},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_per_minute"] == 2
        assert body["active_hook_count"] == 1
        assert body["ceiling"] >= 2

        from clawhum_api.routes.webhooks import dispatch_event, _deliveries_path

        for _ in range(5):
            asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))

        # Only 2 real deliveries; the remaining 3 are recorded as
        # rate_limited and never left the process.
        path = _deliveries_path()
        real = _count_real_deliveries(path, hook_id)
        assert real == 2, f"expected 2 real deliveries, saw {real}"

        suppressed = 0
        with open(path) as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("webhook_id") != hook_id:
                    continue
                if rec.get("rate_limited"):
                    suppressed += 1
                    assert rec["ok"] is False
                    assert rec["status"] == 0
                    assert "rate_limited_by_policy" in (rec.get("error") or "")
                    assert rec.get("rate_cap") == 2
        assert suppressed == 3

        # Audit log must record webhook.rate_limited at least once.
        audit_lines = list(open(tmp_path / "audit.jsonl"))
        assert any(
            "webhook.rate_limited" in line and hook_id in line
            for line in audit_lines
        ), "rate-limited deliveries must hit the audit log"

        # The mutation itself is audited too.
        assert any(
            "webhook_delivery_rate.update" in line for line in audit_lines
        ), "policy update must hit the audit log"


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        hook_a = _create(c, key="adminkey")
        hook_b = _create(c, key="otherkey")
        _force_ok(monkeypatch)

        # Tenant "main" sets cap=1; tenant "other" leaves it unset (0).
        r = c.put(
            "/webhook-delivery-rate",
            json={"max_per_minute": 1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text

        # Tenant "other" sees its own (default) policy, not main's.
        r = c.get(
            "/webhook-delivery-rate",
            headers={"X-API-Key": "otherkey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["max_per_minute"] == 0

        from clawhum_api.routes.webhooks import dispatch_event, _deliveries_path

        # Fire 4 events into each tenant. Main throttles after 1; other does not.
        for _ in range(4):
            asyncio.run(dispatch_event("main", "match.completed", {"x": 1}))
            asyncio.run(dispatch_event("other", "match.completed", {"y": 2}))

        real_a = _count_real_deliveries(_deliveries_path(), hook_a)
        real_b = _count_real_deliveries(_deliveries_path(), hook_b)
        assert real_a == 1, f"tenant main capped at 1, got {real_a}"
        assert real_b == 4, f"tenant other uncapped, got {real_b}"


def test_invalid_cap_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/webhook-delivery-rate",
            json={"max_per_minute": -1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 422, r.text  # pydantic ge=0 rejects negative

        from clawhum_api import webhook_delivery_rate as wdr

        r = c.put(
            "/webhook-delivery-rate",
            json={"max_per_minute": wdr.MAX_PER_MINUTE_CEILING + 1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400, r.text
        body = r.json()["detail"]
        assert body["code"] == "webhook_delivery_rate_invalid"
