"""Tests for the per-workspace data retention policy.

Covers the three procurement-critical guarantees:

1. Default policy keeps everything (opt in only).
2. Setting a TTL hides old rows from history reads and removes them
   from disk on enforce.
3. One tenant cannot view, change, or enforce another tenant's policy
   and an enforcement sweep never touches another tenant's data.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "hist.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ipa.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import retention, ip_allowlist
    retention.reset_cache()
    ip_allowlist.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _write_history(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_default_policy_keeps_everything(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.get("/retention", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["history_days"] == 0
    assert body["audit_days"] == 0


def test_history_hides_expired_rows_and_enforce_purges(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    now = time.time()
    hist = tmp_path / "hist.jsonl"
    _write_history(
        hist,
        [
            {"id": "h_old", "tenant_id": "acme", "created_at": now - (40 * 86400), "ts": now - (40 * 86400), "query": {"name": "old"}, "results": []},
            {"id": "h_new", "tenant_id": "acme", "created_at": now - (1 * 86400), "ts": now - (1 * 86400), "query": {"name": "new"}, "results": []},
        ],
    )

    # With no policy, both visible.
    before = c.get("/history", headers={"X-API-Key": "sk_admin"})
    assert before.status_code == 200
    ids = {row["id"] for row in before.json()["items"]}
    assert ids == {"h_old", "h_new"}

    # Set a 7 day retention.
    upd = c.put(
        "/retention",
        json={"history_days": 7, "feedback_days": 0, "audit_days": 0, "webhook_deliveries_days": 0},
        headers={"X-API-Key": "sk_admin"},
    )
    assert upd.status_code == 200, upd.text

    # Read filtering kicks in immediately, even before enforce runs.
    after = c.get("/history", headers={"X-API-Key": "sk_admin"})
    assert after.status_code == 200
    ids = {row["id"] for row in after.json()["items"]}
    assert ids == {"h_new"}

    # dry_run reports the count without touching disk.
    dry = c.post("/retention/enforce?dry_run=true", headers={"X-API-Key": "sk_admin"})
    assert dry.status_code == 200, dry.text
    assert dry.json()["removed"]["history"] == 1
    raw = hist.read_text(encoding="utf-8")
    assert "h_old" in raw  # not yet deleted

    # Real enforce rewrites the file.
    real = c.post("/retention/enforce", headers={"X-API-Key": "sk_admin"})
    assert real.status_code == 200
    assert real.json()["removed"]["history"] == 1
    raw = hist.read_text(encoding="utf-8")
    assert "h_old" not in raw
    assert "h_new" in raw


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    spec = "acme_ops:sk_acme:9999:admin:acme,globex_ops:sk_globex:9999:admin:globex"
    c = _client(monkeypatch, tmp_path, spec)
    now = time.time()
    hist = tmp_path / "hist.jsonl"
    _write_history(
        hist,
        [
            {"id": "h_acme_old", "tenant_id": "acme", "created_at": now - (40 * 86400), "ts": now - (40 * 86400), "query": {}, "results": []},
            {"id": "h_globex_old", "tenant_id": "globex", "created_at": now - (40 * 86400), "ts": now - (40 * 86400), "query": {}, "results": []},
        ],
    )

    # Acme sets a 7 day policy.
    r = c.put(
        "/retention",
        json={"history_days": 7, "feedback_days": 0, "audit_days": 0, "webhook_deliveries_days": 0},
        headers={"X-API-Key": "sk_acme"},
    )
    assert r.status_code == 200

    # Globex sees a default (all zero) policy, not acme's.
    g = c.get("/retention", headers={"X-API-Key": "sk_globex"})
    assert g.status_code == 200
    assert g.json()["history_days"] == 0

    # Acme enforces a sweep. Globex's old row must survive.
    swept = c.post("/retention/enforce", headers={"X-API-Key": "sk_acme"})
    assert swept.status_code == 200
    assert swept.json()["removed"]["history"] == 1
    raw = hist.read_text(encoding="utf-8")
    assert "h_acme_old" not in raw
    assert "h_globex_old" in raw

    # Globex's own enforce removes nothing because policy is 0.
    g_sweep = c.post("/retention/enforce", headers={"X-API-Key": "sk_globex"})
    assert g_sweep.status_code == 200
    assert g_sweep.json()["removed"]["history"] == 0


def test_non_admin_cannot_read_or_change(monkeypatch, tmp_path):
    spec = "ops:sk_admin:9999:admin:acme,member:sk_member:9999:member:acme"
    c = _client(monkeypatch, tmp_path, spec)
    r = c.get("/retention", headers={"X-API-Key": "sk_member"})
    assert r.status_code == 403
    w = c.put(
        "/retention",
        json={"history_days": 7, "feedback_days": 0, "audit_days": 0, "webhook_deliveries_days": 0},
        headers={"X-API-Key": "sk_member"},
    )
    assert w.status_code == 403


def test_dry_run_preview_returns_all_ui_categories(monkeypatch, tmp_path):
    """Pin the JSON shape consumed by /settings/retention preview UI.

    The page renders one row per category from the ``removed`` map, so
    every category key must be present even when the per-category TTL
    is zero. Empty policy must report all zeros without 5xxing.
    """
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    r = c.post("/retention/enforce?dry_run=true", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["removed"].keys()) == {"history", "feedback", "audit", "webhook_deliveries"}
    assert all(v == 0 for v in body["removed"].values())
    assert body["tenant_id"] == "acme"
    assert isinstance(body["ran_at"], (int, float))
