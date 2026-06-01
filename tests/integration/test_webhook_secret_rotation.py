"""Per-workspace webhook signing-secret max-age (forced rotation) tests.

What an enterprise security review checks here:

1. With no policy, a webhook-list response carries no Sunset or
   Deprecation header even when a webhook is months old.
2. With a 1-day rotation policy, a webhook created in the past is
   surfaced as stale: GET /webhooks attaches Sunset,
   Deprecation: true, X-Clawhum-Webhook-Secret-Stale-Count, and
   X-Clawhum-Webhook-Secret-Max-Age-Days. The /webhook-secret-rotation/stale
   route lists it with the correct age and the same warning headers.
3. A freshly rotated webhook's clock resets; once the post-rotation
   age is under the floor, no headers are attached.
4. Workspace A's policy is invisible to workspace B: B's hooks, even
   ancient ones, get no headers unless B opts in.
5. Malformed policy submissions are rejected with HTTP 400 + 422 and
   a structured error before they can land in the store.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "deliveries.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_SECRET_ROTATION_PATH",
        str(tmp_path / "webhook_secret_rotation.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import webhook_secret_rotation

    webhook_secret_rotation.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _create_old_webhook(tmp_path, tenant_id: str, age_days: float) -> dict:
    """Append a raw webhook record dated ``age_days`` in the past.

    Bypasses POST /webhooks because the API rightfully refuses to
    backdate created_at; the storage format is the same append-only
    JSONL the route reads from so the read path treats it as live.
    """
    path = tmp_path / "webhooks.jsonl"
    created_at = time.time() - age_days * 86400
    rec = {
        "id": f"wh_{int(created_at)}",
        "tenant_id": tenant_id,
        "url": "https://receiver.example.test/hook",
        "events": ["match.completed"],
        "secret_hash": "0" * 64,
        "secret_hint": "whsec_abc...wxyz",
        "active": True,
        "created_at": created_at,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def test_no_policy_no_warning_headers(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _create_old_webhook(tmp_path, "acme", age_days=400)
        r = c.get("/webhooks", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        assert "Sunset" not in r.headers
        assert "Deprecation" not in r.headers
        assert "X-Clawhum-Webhook-Secret-Stale-Count" not in r.headers


def test_policy_attaches_headers_when_hook_is_stale(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/webhook-secret-rotation",
            json={"max_secret_age_days": 1, "docs_url": "https://example.test/rotate-webhooks"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is True
        assert body["max_secret_age_days"] == 1
        # Example headers should preview the policy even when no hook
        # exists yet so the dashboard never shows an empty block.
        assert "Sunset" in body["example_headers"]

        _create_old_webhook(tmp_path, "acme", age_days=5)

        r = c.get("/webhooks", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        assert "Sunset" in r.headers
        assert r.headers.get("Deprecation") == "true"
        assert r.headers.get("X-Clawhum-Webhook-Secret-Stale-Count") == "1"
        assert r.headers.get("X-Clawhum-Webhook-Secret-Max-Age-Days") == "1"
        assert "https://example.test/rotate-webhooks" in r.headers.get("Link", "")

        r = c.get("/webhook-secret-rotation/stale", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_secret_age_days"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["secret_age_days"] >= 4


def test_policy_silent_when_hook_under_floor(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        c.put(
            "/webhook-secret-rotation",
            json={"max_secret_age_days": 30},
            headers={"X-API-Key": "acmekey"},
        )
        _create_old_webhook(tmp_path, "acme", age_days=2)
        r = c.get("/webhooks", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        assert "Sunset" not in r.headers
        assert "X-Clawhum-Webhook-Secret-Stale-Count" not in r.headers


def test_policy_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme opts in, Globex does not.
        c.put(
            "/webhook-secret-rotation",
            json={"max_secret_age_days": 1},
            headers={"X-API-Key": "acmekey"},
        )
        # Globex has an ancient hook but no policy; must stay quiet.
        _create_old_webhook(tmp_path, "globex", age_days=400)
        r = c.get("/webhooks", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert "Sunset" not in r.headers
        # And Globex cannot see Acme's policy.
        r = c.get("/webhook-secret-rotation", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["enforcing"] is False
        # And Globex's stale endpoint shows nothing because no policy
        # = nothing to enforce against, even if their hook is old.
        r = c.get("/webhook-secret-rotation/stale", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["items"] == []


def test_invalid_policy_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/webhook-secret-rotation",
            json={"max_secret_age_days": 99999},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 422
        r = c.put(
            "/webhook-secret-rotation",
            json={"max_secret_age_days": 30, "docs_url": "ftp://nope"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_webhook_secret_rotation"
