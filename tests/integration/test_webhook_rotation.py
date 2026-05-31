from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    """Two tenants: ``main`` (admin) and ``other`` (admin) so we can
    assert cross-tenant rotation is denied while keeping the in-tenant
    path realistic (rotate-secret requires the admin role).
    """
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "webhook_deliveries.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "main:adminkey:10000:admin,other:otherkey:10000:admin",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_rotate_secret_with_grace_window(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://example.com/hook", "events": ["match.completed"]},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        created = r.json()
        hook_id = created["id"]
        original_secret = created["secret"]

        listing = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        original_hint = listing["webhooks"][0]["secret_hint"]
        assert listing["webhooks"][0]["previous_secret_hint"] is None

        r2 = c.post(
            f"/webhooks/{hook_id}/rotate-secret",
            json={"grace_seconds": 3600},
            headers={"X-API-Key": "adminkey"},
        )
        assert r2.status_code == 200, r2.text
        rot = r2.json()
        assert rot["secret"].startswith("whsec_")
        assert rot["secret"] != original_secret
        assert rot["previous_secret_expires_at"]
        assert rot["previous_secret_expires_at"] > time.time()

        # The active hint changed and the previous-hint is exposed so
        # the receiver can recognise both signatures during the window.
        listing2 = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()
        item = listing2["webhooks"][0]
        assert item["secret_hint"] != original_hint
        assert item["previous_secret_hint"] == original_hint
        assert item["previous_secret_expires_at"]
        assert item["rotated_at"]


def test_rotate_zero_grace_invalidates_previous_immediately(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://example.com/hook"},
            headers={"X-API-Key": "adminkey"},
        )
        hook_id = r.json()["id"]

        r2 = c.post(
            f"/webhooks/{hook_id}/rotate-secret",
            json={"grace_seconds": 0},
            headers={"X-API-Key": "adminkey"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["previous_secret_expires_at"] is None

        item = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()["webhooks"][0]
        assert item["previous_secret_hint"] is None
        assert item["previous_secret_expires_at"] is None


def test_rotate_secret_is_tenant_isolated(monkeypatch, tmp_path):
    """A webhook created in tenant ``main`` is invisible to tenant
    ``other``; an attempt to rotate it from the other tenant must 404
    rather than disclose its existence (or, worse, rotate it).
    """
    with _client(monkeypatch, tmp_path) as c:
        created = c.post(
            "/webhooks",
            json={"url": "https://example.com/hook"},
            headers={"X-API-Key": "adminkey"},
        ).json()
        hook_id = created["id"]

        r = c.post(
            f"/webhooks/{hook_id}/rotate-secret",
            json={"grace_seconds": 60},
            headers={"X-API-Key": "otherkey"},
        )
        assert r.status_code == 404

        # And the original tenant's view is unchanged.
        item = c.get("/webhooks", headers={"X-API-Key": "adminkey"}).json()["webhooks"][0]
        assert item["previous_secret_hint"] is None
        assert item["rotated_at"] is None


def test_rotate_requires_admin_role(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "webhook_deliveries.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "main:writerkey:10000:writer,main:adminkey:10000:admin",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    with TestClient(create_app()) as c:
        hook_id = c.post(
            "/webhooks",
            json={"url": "https://example.com/hook"},
            headers={"X-API-Key": "writerkey"},
        ).json()["id"]

        # Writer can create but cannot rotate: rotation is destructive
        # and admin-only, the same posture as delete + MFA.
        r = c.post(
            f"/webhooks/{hook_id}/rotate-secret",
            json={"grace_seconds": 60},
            headers={"X-API-Key": "writerkey"},
        )
        assert r.status_code == 403
