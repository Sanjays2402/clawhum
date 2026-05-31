"""Workspace-level quota enforcement.

The per-key rate limit alone is not enough: a workspace can mint many
keys, so we need an aggregate ceiling per tenant. These tests prove
the workspace ceiling fires even when each individual key is under
its own RPM, and that the X-RateLimit-* headers expose the binding
limit so well-behaved clients can back off.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str, rpm: int = 120):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_QUOTA_PATH", str(tmp_path / "quotas.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", str(rpm))

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import quota_store
    quota_store.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_workspace_ceiling_caps_aggregate_across_keys(monkeypatch, tmp_path):
    """Two keys in the same workspace cannot exceed the workspace plan.

    Each individual key is generous (50 rpm) but the workspace ceiling
    is 3 rpm. After 3 requests in any combination across the two keys
    the next one must 429 with scope=workspace_minute.
    """
    spec = "a:sk_a:50:admin:acme,b:sk_b:50:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec, rpm=120) as c:
        from clawhum_api import quota_store
        quota_store.set_plan(
            tenant_id="acme",
            plan="custom",
            rpm_ceiling=3,
            daily_quota=0,
            actor="test",
        )

        r1 = c.get("/library/tracks", headers={"x-api-key": "sk_a"})
        r2 = c.get("/library/tracks", headers={"x-api-key": "sk_b"})
        r3 = c.get("/library/tracks", headers={"x-api-key": "sk_a"})
        r4 = c.get("/library/tracks", headers={"x-api-key": "sk_b"})
        assert r1.status_code != 429
        assert r2.status_code != 429
        assert r3.status_code != 429
        assert r4.status_code == 429
        assert r4.headers.get("X-RateLimit-Scope") == "workspace_minute"
        assert r4.headers.get("X-RateLimit-Limit") == "3"
        assert r4.headers.get("X-RateLimit-Reset")
        assert r4.headers.get("Retry-After")


def test_workspace_ceiling_isolated_across_tenants(monkeypatch, tmp_path):
    """A different workspace is not affected when one tenant is throttled."""
    spec = "a:sk_a:50:admin:tenA,b:sk_b:50:admin:tenB"
    with _client(monkeypatch, tmp_path, api_keys=spec, rpm=120) as c:
        from clawhum_api import quota_store
        quota_store.set_plan(
            tenant_id="tena",
            plan="custom",
            rpm_ceiling=1,
            daily_quota=0,
            actor="test",
        )

        first = c.get("/library/tracks", headers={"x-api-key": "sk_a"})
        burned = c.get("/library/tracks", headers={"x-api-key": "sk_a"})
        assert first.status_code != 429
        assert burned.status_code == 429
        assert burned.headers.get("X-RateLimit-Scope") == "workspace_minute"

        # tenB has no plan configured so the default (enterprise,
        # unlimited) applies. Must succeed.
        other = c.get("/library/tracks", headers={"x-api-key": "sk_b"})
        assert other.status_code != 429


def test_quota_headers_advertise_binding_limit(monkeypatch, tmp_path):
    """When the workspace ceiling is tighter than the per-key bucket the\n    workspace limit must be the one reported in X-RateLimit-Limit.\n    """
    spec = "a:sk_a:1000:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec, rpm=120) as c:
        from clawhum_api import quota_store
        quota_store.set_plan(
            tenant_id="acme",
            plan="custom",
            rpm_ceiling=10,
            daily_quota=500,
            actor="test",
        )
        r = c.get("/library/tracks", headers={"x-api-key": "sk_a"})
        assert r.status_code != 429
        # Workspace ceiling (10) is tighter than the key (1000) so the
        # binding header must be 10.
        assert r.headers.get("X-RateLimit-Limit") == "10"
        assert r.headers.get("X-RateLimit-Reset")
        assert r.headers.get("X-RateLimit-Plan") == "custom"
        assert r.headers.get("X-RateLimit-Limit-Day") == "500"


def test_quota_admin_route_requires_admin(monkeypatch, tmp_path):
    """A member-role key cannot read or write the plan; admin can read it."""
    spec = "viewer:sk_v:50:viewer:acme,boss:sk_b:50:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec, rpm=120) as c:
        denied = c.get("/quotas", headers={"x-api-key": "sk_v"})
        assert denied.status_code == 403
        ok = c.get("/quotas", headers={"x-api-key": "sk_b"})
        assert ok.status_code == 200
        body = ok.json()
        assert body["plan"]["tenant_id"] == "acme"
        # Catalog must include the canonical plan names so the UI can render.
        names = {entry["name"] for entry in body["catalog"]}
        assert {"free", "team", "business", "enterprise", "custom"} <= names
