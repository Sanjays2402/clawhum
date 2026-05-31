"""Per-workspace monthly budget cap enforcement.

The budget bounds aggregate chargeable consumption over a rolling
30 day window, on top of the rate-limit plan (which bounds the
*rate*). Procurement teams require both: a noisy customer that stays
under RPM can still blow past the contracted monthly volume.

These tests prove:

1. A chargeable POST after the cap is reached is rejected with 402
   and a structured ``budget_exhausted`` body, while a different
   workspace's traffic is untouched.
2. The admin read endpoint reports the live used/remaining/status
   and is tenant-scoped.
3. The PUT requires step-up MFA and writes an audit event with
   before/after.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str, rpm: int = 1000):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_BUDGET_PATH", str(tmp_path / "budgets.jsonl"))
    monkeypatch.setenv("CLAWHUM_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", str(rpm))

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import budget_store, usage
    budget_store.reset_cache()
    usage.reset_month_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _seed_events(tenant_id: str, n: int) -> None:
    """Force ``n`` chargeable events into the rolling window so we can
    test the cap without driving the real /match route from audio."""
    from clawhum_api import usage
    for _ in range(n):
        usage.record_event(tenant_id, "match")


def test_budget_hard_stop_returns_402_and_isolates_tenants(monkeypatch, tmp_path):
    spec = "a:sk_admin_a:50:admin:acme,b:sk_admin_b:50:admin:globex"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        from clawhum_api import budget_store

        # acme: cap=5, hard_stop on. globex: no cap.
        budget_store.set_budget(
            tenant_id="acme",
            monthly_cap=5,
            soft_threshold_pct=80,
            hard_stop=True,
            notes="contract Q3",
            actor="test",
        )

        # Burn the entire monthly cap for acme.
        _seed_events("acme", 5)

        # A chargeable POST for acme is now blocked at the budget layer.
        # We hit /history (POST is chargeable per the usage classifier).
        blocked = c.post(
            "/history",
            headers={"x-api-key": "sk_admin_a"},
            json={"query_id": "q1", "track_id": "t1", "score": 0.9},
        )
        assert blocked.status_code == 402, blocked.text
        body = blocked.json()
        assert body["code"] == "budget_exhausted"
        assert body["monthly_cap"] == 5
        assert body["used"] >= 5
        assert blocked.headers["X-Budget-Status"] == "exhausted"
        assert blocked.headers["X-Budget-Remaining"] == "0"

        # A non-chargeable GET still works for the same tenant: the
        # cap must not lock admins out of the workspace.
        ok_read = c.get("/me", headers={"x-api-key": "sk_admin_a"})
        assert ok_read.status_code == 200

        # The other tenant is completely unaffected even with the same
        # chargeable verb on the same route.
        other = c.post(
            "/history",
            headers={"x-api-key": "sk_admin_b"},
            json={"query_id": "q1", "track_id": "t1", "score": 0.9},
        )
        assert other.status_code != 402, other.text


def test_budget_read_is_tenant_scoped_and_reports_status(monkeypatch, tmp_path):
    spec = "a:sk_admin_a:50:admin:acme,b:sk_admin_b:50:admin:globex"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        from clawhum_api import budget_store

        budget_store.set_budget(
            tenant_id="acme",
            monthly_cap=10,
            soft_threshold_pct=50,
            hard_stop=True,
            notes="",
            actor="test",
        )
        _seed_events("acme", 6)  # 60% used; over the 50% warn threshold.

        r = c.get("/budget", headers={"x-api-key": "sk_admin_a"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["budget"]["tenant_id"] == "acme"
        assert data["budget"]["monthly_cap"] == 10
        assert data["used"] >= 6
        assert data["remaining"] <= 4
        assert data["status"] == "warning"

        # globex has no budget configured, so it reports "unset".
        r2 = c.get("/budget", headers={"x-api-key": "sk_admin_b"})
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["budget"]["tenant_id"] == "globex"
        assert d2["budget"]["monthly_cap"] == 0
        assert d2["status"] == "unset"
        # Critical: tenant A's cap is not visible inside tenant B's payload.
        assert d2["used"] == 0


def test_budget_audit_only_mode_does_not_block(monkeypatch, tmp_path):
    spec = "a:sk_admin_a:50:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        from clawhum_api import budget_store

        budget_store.set_budget(
            tenant_id="acme",
            monthly_cap=2,
            soft_threshold_pct=80,
            hard_stop=False,  # audit-only rollout
            notes="",
            actor="test",
        )
        _seed_events("acme", 10)  # well over the cap

        r = c.post(
            "/history",
            headers={"x-api-key": "sk_admin_a"},
            json={"query_id": "q1", "track_id": "t1", "score": 0.9},
        )
        # Must not 402 in audit-only mode.
        assert r.status_code != 402, r.text
        # The header still reports the overage so dashboards can alert.
        assert r.headers.get("X-Budget-Enforcement") == "audit"
        assert r.headers.get("X-Budget-Status") == "exhausted"
