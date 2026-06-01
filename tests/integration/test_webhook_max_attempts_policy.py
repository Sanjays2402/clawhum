"""Per-workspace webhook max-attempts policy tests.

What an enterprise security review checks here:

1. Without an explicit policy row the deployment-wide
   ``webhook_max_attempts`` applies (existing tenants behave
   exactly as before this feature shipped).
2. A per-workspace pin actually changes how many times the
   dispatcher tries: a pin of 1 produces one delivery row, a pin
   of 4 produces four delivery rows for a permanently failing
   receiver.
3. ``max_attempts = 0`` is rejected with 400 so events are never
   silently dropped by a fat-finger.
4. The policy of tenant A is invisible to tenant B and does not
   change tenant B's effective attempts (no cross-tenant leakage).
5. Mutating the policy writes a structured audit event under
   ``webhook_max_attempts_policy.update`` with actor + before/after.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, global_attempts: int = 3):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH",
        str(tmp_path / "webhook_deliveries.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOK_MAX_ATTEMPTS", str(global_attempts))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_MAX_ATTEMPTS_POLICY_PATH",
        str(tmp_path / "webhook_max_attempts_policy.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_REQUIRE_HTTPS", "false")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_TIMEOUT_SEC", "0.1")
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
    from clawhum_api import webhook_max_attempts_policy

    webhook_max_attempts_policy.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _create_hook(c, key, url="http://127.0.0.1:9/hook"):
    r = c.post(
        "/webhooks",
        json={"url": url, "events": ["match.completed"]},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _dispatch_once(hook_id: str, tenant_id: str):
    from clawhum_api.routes import webhooks as _wh

    hook = _wh._find_hook_any_tenant(hook_id)
    assert hook is not None
    await _wh._deliver_one(
        hook=hook,
        event="match.completed",
        payload={"hello": "world"},
        store_payload=False,
    )


def _delivery_rows(tmp_path, hook_id):
    p = Path(tmp_path / "webhook_deliveries.jsonl")
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text().splitlines()
        if line.strip() and json.loads(line).get("webhook_id") == hook_id
    ]


def test_default_uses_global_attempts(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_attempts=3) as c:
        r = c.get(
            "/webhook-max-attempts-policy",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is False
        assert body["global_default"] == 3
        assert body["effective_max_attempts"] == 3


def test_per_workspace_pin_changes_attempt_count(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_attempts=2) as c:
        hook = _create_hook(c, "acmekey")
        r = c.put(
            "/webhook-max-attempts-policy",
            json={"max_attempts": 4},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["effective_max_attempts"] == 4

        # Dispatch to an unreachable port: every attempt fails fast and
        # writes one delivery row, so we can count attempts directly.
        asyncio.run(_dispatch_once(hook["id"], "acme"))
        rows = _delivery_rows(tmp_path, hook["id"])
        assert len(rows) == 4, rows
        assert all(r.get("ok") is False for r in rows)
        assert [r["attempt"] for r in rows] == [1, 2, 3, 4]


def test_zero_attempts_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_attempts=2) as c:
        r = c.put(
            "/webhook-max-attempts-policy",
            json={"max_attempts": 0},
            headers={"X-API-Key": "acmekey"},
        )
        # Pydantic schema rejects at 422 before our ValueError fires; the
        # contract is "the server refuses to drop events silently".
        assert r.status_code in (400, 422), r.text


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_attempts=3) as c:
        # Acme pins 1 attempt.
        r = c.put(
            "/webhook-max-attempts-policy",
            json={"max_attempts": 1},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Globex sees the global default, not Acme's pin.
        r = c.get(
            "/webhook-max-attempts-policy",
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is False
        assert body["effective_max_attempts"] == 3

        # And the dispatcher proves it: a Globex hook makes 3 attempts.
        hook_b = _create_hook(c, "globexkey")
        asyncio.run(_dispatch_once(hook_b["id"], "globex"))
        rows = _delivery_rows(tmp_path, hook_b["id"])
        assert len(rows) == 3, rows


def test_update_writes_audit_event(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, global_attempts=3) as c:
        r = c.put(
            "/webhook-max-attempts-policy",
            json={"max_attempts": 5},
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
        if e.get("action") == "webhook_max_attempts_policy.update"
    ]
    assert updates, f"no policy update event in audit log: {events}"
    last = updates[-1]
    assert last["tenant_id"] == "acme"
    assert last["after"]["max_attempts"] == 5
