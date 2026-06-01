"""Tests for the sub-processor registry, per-workspace acknowledgement,
and notification subscription endpoints.

Covers what an enterprise procurement and EU data protection review
actually asks for:

1. The global registry is readable by every authed role.
2. Only platform admin workspaces may mutate the global list. Other
   admins get 403 even with full admin role and MFA.
3. Acknowledgements and subscriptions are tenant scoped. Tenant A
   cannot see or modify tenant B's acknowledgement or its email list.
4. Acknowledgement rejects a stale revision with 409 so a customer
   cannot blindly accept a list that has changed under them.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str, platform_tenants: str = ""):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_SUBPROCESSORS_PATH", str(tmp_path / "sp.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_SUBPROCESSOR_TENANT_PATH", str(tmp_path / "sp_tenant.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_SUBPROCESSORS_PLATFORM_ADMIN_TENANTS", platform_tenants
    )
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import subprocessors
    subprocessors.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _hdr(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def test_platform_admin_can_crud_and_others_can_read(monkeypatch, tmp_path):
    keys = (
        "ops:sk_platform:9999:admin:platform,"
        "ops:sk_tenant:9999:admin:acme,"
        "ops:sk_view:9999:reader:acme"
    )
    c = _client(monkeypatch, tmp_path, keys, platform_tenants="platform")

    # Empty registry, every authed role can read.
    for k in ("sk_platform", "sk_tenant", "sk_view"):
        r = c.get("/subprocessors", headers=_hdr(k))
        assert r.status_code == 200, (k, r.text)
        body = r.json()
        assert body["processors"] == []
        assert body["revision"] == 0
        assert "active" in body["statuses"]
        assert body["can_manage"] is (k == "sk_platform")

    # Tenant admin (non-platform) cannot write even though role is admin.
    blocked = c.post(
        "/subprocessors",
        json={
            "name": "Stripe",
            "purpose": "Billing",
            "region": "US",
            "data_categories": ["billing email"],
            "dpa_url": "https://stripe.com/legal/dpa",
        },
        headers=_hdr("sk_tenant"),
    )
    assert blocked.status_code == 403, blocked.text

    # Platform admin can create.
    created = c.post(
        "/subprocessors",
        json={
            "name": "Stripe",
            "purpose": "Billing",
            "region": "US",
            "data_categories": ["billing email", "card last4"],
            "dpa_url": "https://stripe.com/legal/dpa",
            "status": "active",
        },
        headers=_hdr("sk_platform"),
    )
    assert created.status_code == 201, created.text
    sp_id = created.json()["id"]
    assert created.json()["data_categories"] == ["billing email", "card last4"]

    listed = c.get("/subprocessors", headers=_hdr("sk_view")).json()
    assert listed["revision"] == 1
    assert len(listed["processors"]) == 1
    assert listed["processors"][0]["id"] == sp_id

    # Patch bumps revision.
    upd = c.patch(
        f"/subprocessors/{sp_id}",
        json={"region": "US, EU"},
        headers=_hdr("sk_platform"),
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["region"] == "US, EU"
    rev_after_patch = c.get("/subprocessors", headers=_hdr("sk_view")).json()["revision"]
    assert rev_after_patch == 2

    # Bad URL rejected with 400.
    bad = c.post(
        "/subprocessors",
        json={"name": "Foo", "dpa_url": "not-a-url"},
        headers=_hdr("sk_platform"),
    )
    assert bad.status_code == 400


def test_acknowledgement_is_tenant_scoped_and_revision_locked(monkeypatch, tmp_path):
    keys = (
        "ops:sk_platform:9999:admin:platform,"
        "ops:sk_a:9999:admin:acme,"
        "ops:sk_b:9999:admin:beta"
    )
    c = _client(monkeypatch, tmp_path, keys, platform_tenants="platform")

    # Seed two sub-processors so revision is 2.
    for name in ("Stripe", "Datadog"):
        r = c.post(
            "/subprocessors",
            json={"name": name, "dpa_url": "https://example.com/dpa"},
            headers=_hdr("sk_platform"),
        )
        assert r.status_code == 201

    current = c.get("/subprocessors", headers=_hdr("sk_a")).json()["revision"]
    assert current == 2

    # Stale revision rejected.
    stale = c.post(
        "/subprocessors/acknowledgement",
        json={"revision": 1},
        headers=_hdr("sk_a"),
    )
    assert stale.status_code == 409

    # Acme acknowledges, beta has not.
    ok = c.post(
        "/subprocessors/acknowledgement",
        json={"revision": current},
        headers=_hdr("sk_a"),
    )
    assert ok.status_code == 200
    assert ok.json()["up_to_date"] is True
    assert ok.json()["revision"] == current

    a_view = c.get("/subprocessors/acknowledgement", headers=_hdr("sk_a")).json()
    assert a_view["up_to_date"] is True
    assert a_view["revision"] == current

    b_view = c.get("/subprocessors/acknowledgement", headers=_hdr("sk_b")).json()
    # Beta has no record, must not see acme's acknowledgement.
    assert b_view["revision"] == 0
    assert b_view["up_to_date"] is False
    assert b_view["current_revision"] == current
    assert b_view["acknowledged_by"] == ""

    # A new mutation invalidates acme's ack.
    c.post(
        "/subprocessors",
        json={"name": "Sentry", "dpa_url": "https://sentry.io/legal/dpa/"},
        headers=_hdr("sk_platform"),
    )
    a_after = c.get("/subprocessors/acknowledgement", headers=_hdr("sk_a")).json()
    assert a_after["up_to_date"] is False
    assert a_after["current_revision"] == current + 1
    assert a_after["revision"] == current


def test_subscriptions_are_tenant_scoped(monkeypatch, tmp_path):
    keys = (
        "ops:sk_a:9999:admin:acme,"
        "ops:sk_b:9999:admin:beta"
    )
    c = _client(monkeypatch, tmp_path, keys)

    # Acme adds two subscribers.
    r1 = c.post(
        "/subprocessors/subscriptions",
        json={"email": "dpo@acme.com"},
        headers=_hdr("sk_a"),
    )
    assert r1.status_code == 201
    sub_id = r1.json()["id"]
    r2 = c.post(
        "/subprocessors/subscriptions",
        json={"email": "legal@acme.com"},
        headers=_hdr("sk_a"),
    )
    assert r2.status_code == 201

    # Duplicate rejected.
    dup = c.post(
        "/subprocessors/subscriptions",
        json={"email": "dpo@acme.com"},
        headers=_hdr("sk_a"),
    )
    assert dup.status_code == 400

    # Bad email rejected.
    bad = c.post(
        "/subprocessors/subscriptions",
        json={"email": "not-an-email"},
        headers=_hdr("sk_a"),
    )
    assert bad.status_code == 400

    # Beta sees nothing.
    b_list = c.get("/subprocessors/subscriptions", headers=_hdr("sk_b")).json()
    assert b_list["subscriptions"] == []

    # Beta cannot delete acme's subscription.
    fail = c.delete(
        f"/subprocessors/subscriptions/{sub_id}",
        headers=_hdr("sk_b"),
    )
    assert fail.status_code == 404

    # Acme can.
    ok = c.delete(
        f"/subprocessors/subscriptions/{sub_id}",
        headers=_hdr("sk_a"),
    )
    assert ok.status_code == 204

    final = c.get("/subprocessors/subscriptions", headers=_hdr("sk_a")).json()
    assert len(final["subscriptions"]) == 1
    assert final["subscriptions"][0]["email"] == "legal@acme.com"


def test_v1_alias_is_mounted(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk:9999:admin:acme")
    r = c.get("/v1/subprocessors", headers=_hdr("sk"))
    assert r.status_code == 200
    assert r.json()["processors"] == []
