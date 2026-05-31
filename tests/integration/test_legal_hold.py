"""Integration tests for per-workspace legal hold (litigation hold)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_SEAT_LIMITS_PATH", str(tmp_path / "seats.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_SSO_STORE_PATH", str(tmp_path / "sso.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ip.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "acme_reader:acmereader:10000:reader:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import legal_hold
    legal_hold.reset_cache()
    from clawhum_api import retention
    retention.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_status_starts_clear(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/legal-holds/status", headers={"X-API-Key": "acmereader"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == "acme"
        assert body["on_hold"] is False


def test_reader_cannot_place_hold(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/legal-holds",
            json={"reason": "should be blocked"},
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403


def test_place_hold_blocks_history_delete_and_release_restores(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/history", json={"query": "hummed"}, headers={"X-API-Key": "acmekey"})
        assert r.status_code in (200, 201), r.text
        hid = r.json()["id"]

        r = c.post(
            "/legal-holds",
            json={"reason": "Litigation Smith v. Acme"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        hold_id = r.json()["id"]
        assert r.json()["active"] is True

        r = c.get("/legal-holds/status", headers={"X-API-Key": "acmekey"})
        assert r.json()["on_hold"] is True
        assert r.json()["active_hold_id"] == hold_id

        r = c.delete(f"/history/{hid}", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 423, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "legal_hold_active"
        assert detail["hold_id"] == hold_id

        r = c.delete("/privacy/me", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 423, r.text
        assert r.json()["detail"]["error"] == "legal_hold_active"

        rr = c.put(
            "/retention",
            json={
                "history_days": 1,
                "feedback_days": 0,
                "audit_days": 0,
                "webhook_deliveries_days": 0,
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert rr.status_code == 200, rr.text
        r = c.post("/retention/enforce", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 423, r.text
        r = c.post("/retention/enforce?dry_run=true", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text

        r = c.post(f"/legal-holds/{hold_id}/release", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        assert r.json()["active"] is False
        assert r.json()["released_at"]

        r = c.delete(f"/history/{hid}", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text

        r = c.get("/legal-holds", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        ids = [h["id"] for h in r.json()["holds"]]
        assert hold_id in ids


def test_hold_is_strictly_per_tenant(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/legal-holds",
            json={"reason": "Acme matter"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        acme_hold_id = r.json()["id"]

        r = c.get("/legal-holds/status", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["on_hold"] is False

        r = c.get("/legal-holds", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        ids = [h["id"] for h in r.json()["holds"]]
        assert acme_hold_id not in ids

        r = c.post(f"/legal-holds/{acme_hold_id}/release", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 404

        r = c.post("/history", json={"query": "globex hum"}, headers={"X-API-Key": "globexkey"})
        assert r.status_code in (200, 201), r.text
        gid = r.json()["id"]
        r = c.delete(f"/history/{gid}", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text

        r = c.get("/legal-holds/status", headers={"X-API-Key": "acmekey"})
        assert r.json()["on_hold"] is True
        assert r.json()["active_hold_id"] == acme_hold_id


def test_underlying_retention_enforce_raises_when_on_hold(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path):
        from clawhum_api import legal_hold, retention

        legal_hold.place_hold("acme", reason="preservation order", actor="test")
        retention.set_policy(
            "acme",
            history_days=1,
            feedback_days=0,
            audit_days=0,
            webhook_deliveries_days=0,
            updated_by="test",
        )
        try:
            retention.enforce_policy("acme")
        except legal_hold.LegalHoldActive as e:
            assert e.hold.tenant_id == "acme"
        else:
            raise AssertionError("expected LegalHoldActive")
