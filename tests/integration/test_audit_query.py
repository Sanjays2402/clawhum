"""Tests for the workspace audit log query and export.

Covers procurement critical guarantees:

1. Admin can search their own workspace's events.
2. Members are denied (403) on both list and export.
3. Cross tenant: one tenant never sees another's events on either
   the list or the export endpoints, even if they construct queries
   designed to pull other rows in.
4. CSV and JSON export return only the caller's rows and the
   filter set behaves consistently with the list endpoint.
5. Rotated audit siblings (audit.jsonl.1) are walked too, so the
   admin view is complete across rotations.
"""

from __future__ import annotations

import csv
import io
import json
import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "hist.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ipa.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import ip_allowlist, retention
    ip_allowlist.reset_cache()
    retention.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _seed_events(path, events):
    with path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_admin_can_list_own_events(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    now = time.time()
    _seed_events(
        tmp_path / "audit.jsonl",
        [
            {"ts": now - 10, "actor": "key:abc", "tenant_id": "acme",
             "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]},
            {"ts": now - 5, "actor": "key:abc", "tenant_id": "acme",
             "method": "DELETE", "path": "/keys/k_1", "status": 204, "roles": ["admin"]},
        ],
    )
    r = c.get("/audit", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    # Newest first.
    assert body["items"][0]["path"] == "/keys/k_1"
    assert body["items"][1]["method"] == "POST"


def test_member_is_denied_on_list_and_export(monkeypatch, tmp_path):
    spec = "ops:sk_admin:9999:admin:acme,m:sk_member:9999:member:acme"
    c = _client(monkeypatch, tmp_path, spec)
    _seed_events(
        tmp_path / "audit.jsonl",
        [{"ts": time.time(), "actor": "x", "tenant_id": "acme",
          "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]}],
    )
    r1 = c.get("/audit", headers={"X-API-Key": "sk_member"})
    assert r1.status_code == 403, r1.text
    r2 = c.get("/audit/export", headers={"X-API-Key": "sk_member"})
    assert r2.status_code == 403, r2.text


def test_cross_tenant_isolation_on_list_and_export(monkeypatch, tmp_path):
    spec = "a:sk_acme:9999:admin:acme,g:sk_globex:9999:admin:globex"
    c = _client(monkeypatch, tmp_path, spec)
    now = time.time()
    _seed_events(
        tmp_path / "audit.jsonl",
        [
            {"ts": now - 30, "actor": "key:acme1", "tenant_id": "acme",
             "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]},
            {"ts": now - 20, "actor": "key:globex1", "tenant_id": "globex",
             "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]},
            {"ts": now - 10, "actor": "anonymous", "tenant_id": None,
             "method": "POST", "path": "/match", "status": 401, "roles": []},
        ],
    )
    # Acme sees only its own row, even when probing for globex's path.
    r = c.get("/audit?path=/keys", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["tenant_id"] == "acme"
    assert items[0]["actor"] == "key:acme1"

    # Globex sees only its own row.
    r = c.get("/audit", headers={"X-API-Key": "sk_globex"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["tenant_id"] == "globex"

    # CSV export for acme contains only acme's tenant id; globex's actor
    # must not appear anywhere in the body.
    exp = c.get("/audit/export?format=csv", headers={"X-API-Key": "sk_acme"})
    assert exp.status_code == 200
    body = exp.content.decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(body)))
    assert reader, "csv must contain at least the seeded row"
    assert all(row["tenant_id"] == "acme" for row in reader)
    assert "globex1" not in body
    assert exp.headers.get("X-Audit-Truncated") == "0"

    # JSON export shape and isolation.
    expj = c.get("/audit/export?format=json", headers={"X-API-Key": "sk_acme"})
    assert expj.status_code == 200
    payload = json.loads(expj.content.decode("utf-8"))
    assert payload["tenant_id"] == "acme"
    assert payload["count"] == len(payload["items"])
    assert all(it["tenant_id"] == "acme" for it in payload["items"])


def test_filters_apply_to_list(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "a:sk_acme:9999:admin:acme")
    now = time.time()
    _seed_events(
        tmp_path / "audit.jsonl",
        [
            {"ts": now - 100, "actor": "key:1", "tenant_id": "acme",
             "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]},
            {"ts": now - 50, "actor": "key:1", "tenant_id": "acme",
             "method": "DELETE", "path": "/keys/k_x", "status": 500, "roles": ["admin"]},
            {"ts": now - 10, "actor": "key:2", "tenant_id": "acme",
             "method": "POST", "path": "/webhooks", "status": 200, "roles": ["admin"], "dry_run": True},
        ],
    )
    # Method filter
    r = c.get("/audit?method=DELETE", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == 500

    # Path prefix
    r = c.get("/audit?path=/webhooks", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["path"] == "/webhooks"

    # status_min filter
    r = c.get("/audit?status_min=500", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200
    assert all(it["status"] >= 500 for it in r.json()["items"])

    # dry_run only
    r = c.get("/audit?dry_run=only", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["dry_run"] is True

    # Actor substring
    r = c.get("/audit?actor=key:2", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_rotated_siblings_are_included(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "a:sk_acme:9999:admin:acme")
    now = time.time()
    # Rotated sibling holds the older event.
    _seed_events(
        tmp_path / "audit.jsonl.1",
        [{"ts": now - 1000, "actor": "key:old", "tenant_id": "acme",
          "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]}],
    )
    # Active file holds the new event.
    _seed_events(
        tmp_path / "audit.jsonl",
        [{"ts": now - 1, "actor": "key:new", "tenant_id": "acme",
          "method": "POST", "path": "/keys", "status": 201, "roles": ["admin"]}],
    )
    r = c.get("/audit", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200, r.text
    body = r.json()
    actors = [it["actor"] for it in body["items"]]
    assert "key:new" in actors and "key:old" in actors
    # Newest first.
    assert actors[0] == "key:new"


def test_pagination(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "a:sk_acme:9999:admin:acme")
    now = time.time()
    _seed_events(
        tmp_path / "audit.jsonl",
        [
            {"ts": now - i, "actor": f"key:{i}", "tenant_id": "acme",
             "method": "POST", "path": "/keys", "status": 200, "roles": ["admin"]}
            for i in range(5)
        ],
    )
    r = c.get("/audit?limit=2&offset=0", headers={"X-API-Key": "sk_acme"})
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    r2 = c.get("/audit?limit=2&offset=2", headers={"X-API-Key": "sk_acme"})
    assert r2.status_code == 200
    page2 = r2.json()
    assert len(page2["items"]) == 2
    # Different pages, no overlap.
    a = {it["actor"] for it in page["items"]}
    b = {it["actor"] for it in page2["items"]}
    assert a.isdisjoint(b)
