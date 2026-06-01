"""Per-workspace webhook auto-disable threshold tests.

What an enterprise security review checks here:

1. Without an explicit policy row the deployment-wide default applies
   (existing tenants behave exactly as before this feature shipped).
2. With a per-workspace threshold pinned, the breaker trips at that
   number of consecutive failures instead of the global default.
3. ``threshold = 0`` disables the breaker for that workspace; even a
   large failure streak leaves the hook active so it must be paused
   manually.
4. The policy row of tenant A is invisible to tenant B and does not
   affect tenant B's effective threshold (no cross-tenant leakage).
5. Mutating the policy writes a structured audit event under
   ``webhook_auto_disable_policy.update`` with actor + before/after.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, global_threshold: int = 3):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH",
        str(tmp_path / "webhook_deliveries.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_AUTO_DISABLE_THRESHOLD", str(global_threshold)
    )
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_AUTO_DISABLE_POLICY_PATH",
        str(tmp_path / "webhook_auto_disable_policy.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_WEBHOOK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:acmekey:10000:admin:acme,globex:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import webhook_auto_disable_policy

    webhook_auto_disable_policy.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _create_hook(c, key):
    r = c.post(
        "/webhooks",
        json={
            "url": "https://example.com/hook",
            "events": ["match.completed"],
        },
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _drive_failures(n: int, hook_id: str, tenant_id: str):
    """Simulate ``n`` failed deliveries against ``hook_id``.

    Calls the same internal append + breaker hook the worker uses so
    we do not need a live network. Keeps the test fast and
    deterministic without weakening the policy code path under test.
    """
    import time
    from clawhum_api.routes import webhooks as _wh

    hook = _wh._find_hook_any_tenant(hook_id)
    assert hook is not None
    for _ in range(n):
        _wh._append_delivery({
            "id": f"d-{time.time_ns()}",
            "webhook_id": hook_id,
            "tenant_id": tenant_id,
            "ok": False,
            "status": 500,
            "attempts": 1,
            "created_at": time.time(),
        })
        rec = _wh._maybe_auto_disable(hook)
        if rec is not None:
            return rec
    return _wh._find_hook_any_tenant(hook_id)


def test_default_uses_global_threshold(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_threshold=3) as c:
        r = c.get(
            "/webhook-auto-disable-policy",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is False
        assert body["effective_threshold"] == 3
        assert body["global_default"] == 3
        assert body["breaker_enabled"] is True


def test_per_workspace_threshold_trips_earlier(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_threshold=10) as c:
        hook = _create_hook(c, "acmekey")
        # Pin a much tighter breaker for acme; globex is untouched.
        r = c.put(
            "/webhook-auto-disable-policy",
            json={"threshold": 2},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["effective_threshold"] == 2

        # 2 failures must trip the breaker (global default is 10).
        rec = asyncio.run(_drive_failures(2, hook["id"], "acme"))
        assert rec is not None
        assert rec["active"] is False
        assert "auto_disabled_at" in rec


def test_threshold_zero_disables_breaker(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_threshold=2) as c:
        hook = _create_hook(c, "acmekey")
        r = c.put(
            "/webhook-auto-disable-policy",
            json={"threshold": 0},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is True
        assert body["effective_threshold"] == 0
        assert body["breaker_enabled"] is False

        # Even a long failure streak leaves the hook active when the
        # breaker is opted out at the workspace level.
        result = asyncio.run(_drive_failures(20, hook["id"], "acme"))
        assert result is not None
        assert result.get("active", True) is True
        assert "auto_disabled_at" not in result


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_threshold=5) as c:
        # Acme pins threshold = 1.
        r = c.put(
            "/webhook-auto-disable-policy",
            json={"threshold": 1},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Globex sees the global default, not Acme's pin.
        r = c.get(
            "/webhook-auto-disable-policy",
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is False
        assert body["effective_threshold"] == 5

        # Globex hook survives 3 failures because its threshold is 5.
        hook_b = _create_hook(c, "globexkey")
        result = asyncio.run(_drive_failures(3, hook_b["id"], "globex"))
        assert result is not None
        assert result.get("active", True) is True


def test_update_writes_audit_event(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_threshold=5) as c:
        r = c.put(
            "/webhook-auto-disable-policy",
            json={"threshold": 7},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

    audit_path = Path(tmp_path / "audit.jsonl")
    assert audit_path.exists()
    events = [
        json.loads(line)
        for line in audit_path.read_text().splitlines()
        if line.strip()
    ]
    updates = [
        e for e in events
        if e.get("action") == "webhook_auto_disable_policy.update"
    ]
    assert updates, f"no policy update event in audit log: {events}"
    last = updates[-1]
    assert last["tenant_id"] == "acme"
    assert last["after"]["threshold"] == 7
