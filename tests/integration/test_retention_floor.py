"""Per-workspace retention floor enforcement tests.

What an enterprise security review checks here:

1. With no floor, ``PUT /retention`` accepts any in-range value
   (existing tenants are unaffected; opt-in feature).
2. With a floor pinned, ``PUT /retention`` rejects any positive
   value below the floor with HTTP 400 ``retention_floor_violation``
   naming the offending field, while value ``0`` (keep forever) is
   always allowed because it strictly increases retention.
3. Tenant A's floor is invisible to tenant B and does NOT block
   tenant B's retention updates (no cross-tenant leakage).
4. Mutating the floor writes a structured audit event under
   ``retention_floor.update`` with actor + before/after.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ip_allowlist.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_RETENTION_FLOOR_PATH", str(tmp_path / "retention_floor.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import retention_floor

    retention_floor.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _put_floor(c, key, **kwargs):
    body = {
        "history_days": 0,
        "feedback_days": 0,
        "audit_days": 0,
        "webhook_deliveries_days": 0,
    }
    body.update(kwargs)
    return c.put("/retention-floor", json=body, headers={"X-API-Key": key})


def _put_retention(c, key, **kwargs):
    body = {
        "history_days": 0,
        "feedback_days": 0,
        "audit_days": 0,
        "webhook_deliveries_days": 0,
    }
    body.update(kwargs)
    return c.put("/retention", json=body, headers={"X-API-Key": key})


def test_no_floor_means_no_restriction(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/retention-floor", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["history_days"] == 0
        assert body["audit_days"] == 0

        # PUT /retention with any in-range value succeeds.
        r = _put_retention(c, "acmekey", audit_days=7, history_days=30)
        assert r.status_code == 200, r.text
        assert r.json()["audit_days"] == 7


def test_floor_blocks_below_but_allows_zero(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Pin audit floor at 90 days.
        r = _put_floor(c, "acmekey", audit_days=90, webhook_deliveries_days=30)
        assert r.status_code == 200, r.text
        assert r.json()["audit_days"] == 90

        # Below floor for audit_days -> 400 retention_floor_violation.
        r = _put_retention(c, "acmekey", audit_days=7)
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "retention_floor_violation"
        assert "audit_days" in detail["message"]

        # Multiple violations are reported together.
        r = _put_retention(c, "acmekey", audit_days=7, webhook_deliveries_days=5)
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert "audit_days" in detail["message"]
        assert "webhook_deliveries_days" in detail["message"]

        # At-floor is accepted.
        r = _put_retention(c, "acmekey", audit_days=90, webhook_deliveries_days=30)
        assert r.status_code == 200, r.text

        # Above floor is accepted.
        r = _put_retention(c, "acmekey", audit_days=365, webhook_deliveries_days=180)
        assert r.status_code == 200, r.text

        # 0 (keep forever) is always allowed because it strictly
        # increases retention.
        r = _put_retention(c, "acmekey", audit_days=0, webhook_deliveries_days=0)
        assert r.status_code == 200, r.text


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Tenant acme pins a strict floor.
        r = _put_floor(c, "acmekey", audit_days=365)
        assert r.status_code == 200, r.text

        # Tenant globex sees no floor.
        r = c.get("/retention-floor", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        assert r.json()["audit_days"] == 0

        # Tenant globex can set retention as low as it likes; acme's
        # floor must not affect another workspace.
        r = _put_retention(c, "globexkey", audit_days=1)
        assert r.status_code == 200, r.text
        assert r.json()["audit_days"] == 1

        # Tenant acme is still blocked by its own floor.
        r = _put_retention(c, "acmekey", audit_days=1)
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "retention_floor_violation"


def test_floor_mutation_is_audit_logged(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = _put_floor(c, "acmekey", audit_days=180)
        assert r.status_code == 200, r.text

        audit_path = Path(tmp_path / "audit.jsonl")
        assert audit_path.exists(), "audit log was not written"
        entries = [
            json.loads(line)
            for line in audit_path.read_text().splitlines()
            if line.strip()
        ]
        updates = [e for e in entries if e.get("action") == "retention_floor.update"]
        assert updates, f"expected retention_floor.update entry, got {entries}"
        last = updates[-1]
        assert last["tenant_id"] == "acme"
        assert last["after"]["audit_days"] == 180
        assert last["before"]["audit_days"] == 0
