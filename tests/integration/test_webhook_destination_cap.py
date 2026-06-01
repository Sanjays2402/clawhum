"""Per-workspace webhook destination cap enforcement tests.

What an enterprise security review checks here:

1. Without an explicit policy the legacy default (20) applies so
   existing tenants are not silently allowed to grow unbounded.
2. With a cap set, the (N+1)-th create is rejected at /webhooks
   with HTTP 429 and a structured body that names live and
   max_active so dashboards can show why.
3. Deleting a destination frees a slot so the next create succeeds.
4. Tenant A's cap is invisible to tenant B and does not block
   tenant B's creates (no cross-tenant leakage at the policy layer).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH",
        str(tmp_path / "webhook_deliveries.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DESTINATION_CAP_PATH",
        str(tmp_path / "webhook_destination_cap.jsonl"),
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    # Production SSRF block would reject example.com because of DNS
    # rebinding tests in other modules. Disable here; SSRF has its
    # own dedicated suite.
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import webhook_destination_cap

    webhook_destination_cap.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _create(c, key, idx):
    return c.post(
        "/webhooks",
        json={
            "url": f"https://example.com/hook{idx}",
            "events": ["match.completed"],
        },
        headers={"X-API-Key": key},
    )


def test_default_cap_is_legacy_soft_limit(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get(
            "/webhook-destination-cap", headers={"X-API-Key": "acmekey"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is False
        assert body["effective_cap"] == body["default_cap"] == 20
        assert body["enforcing"] is True


def test_cap_blocks_extra_with_429(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/webhook-destination-cap",
            json={"max_active": 2},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enforcing"] is True
        assert body["max_active"] == 2

        ids = []
        for i in range(2):
            r = _create(c, "acmekey", i)
            assert r.status_code == 200, r.text
            ids.append(r.json()["id"])

        # Third create is rejected with structured 429
        r = _create(c, "acmekey", 99)
        assert r.status_code == 429, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "webhook_destination_cap_exceeded"
        assert detail["max_active"] == 2
        assert detail["live"] >= 2

        # Deleting one frees a slot
        rev = c.delete(
            f"/webhooks/{ids[0]}", headers={"X-API-Key": "acmekey"}
        )
        assert rev.status_code in (200, 204), rev.text
        r = _create(c, "acmekey", 100)
        assert r.status_code == 200, r.text


def test_cap_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme pins a cap of 1
        r = c.put(
            "/webhook-destination-cap",
            json={"max_active": 1},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Globex sees default cap (20), no leakage from Acme's policy
        r = c.get(
            "/webhook-destination-cap",
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["explicit"] is False
        assert r.json()["effective_cap"] == 20

        # Globex can create several destinations freely
        for i in range(3):
            r = _create(c, "globexkey", i)
            assert r.status_code == 200, r.text

        # Acme still capped at 1 regardless of Globex activity
        r = _create(c, "acmekey", 1)
        assert r.status_code == 200, r.text
        r = _create(c, "acmekey", 2)
        assert r.status_code == 429, r.text
        assert (
            r.json()["detail"]["error"]
            == "webhook_destination_cap_exceeded"
        )


def test_opt_out_falls_back_to_global_ceiling(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # max_active=0 means "no per-workspace cap"; the global hard
        # ceiling (MAX_CAP) still applies but is far above any test.
        r = c.put(
            "/webhook-destination-cap",
            json={"max_active": 0},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["explicit"] is True
        assert body["max_active"] == 0
        assert body["effective_cap"] == body["max_allowed"]

        # Should be able to comfortably exceed the legacy soft cap
        # of 20 once opted out.
        for i in range(22):
            r = _create(c, "acmekey", i)
            assert r.status_code == 200, r.text
