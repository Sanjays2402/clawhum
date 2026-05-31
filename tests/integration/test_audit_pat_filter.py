"""Audit log can be filtered by pat_id and session_id for incident response.

When a PAT is implicated in an incident the workspace admin must be
able to list every action that token performed, no matter what
endpoint it hit or what name the PAT had at the time. We persist
``pat_id`` and ``session_id`` on every audit event and expose them
both as filter query params and as columns on the CSV export.
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


def _seed(path, events):
    with path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_filter_by_pat_id_isolates_token_activity(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    now = time.time()
    _seed(
        tmp_path / "audit.jsonl",
        [
            {"ts": now - 30, "actor": "key:a", "tenant_id": "acme", "pat_id": "pat_111",
             "api_key_name": "pat:ci", "method": "POST", "path": "/match", "status": 200, "roles": ["member"]},
            {"ts": now - 20, "actor": "key:a", "tenant_id": "acme", "pat_id": "pat_111",
             "api_key_name": "pat:ci", "method": "POST", "path": "/library", "status": 201, "roles": ["member"]},
            {"ts": now - 10, "actor": "key:b", "tenant_id": "acme", "pat_id": "pat_222",
             "api_key_name": "pat:ops", "method": "DELETE", "path": "/library/1", "status": 204, "roles": ["admin"]},
        ],
    )
    r = c.get("/v1/audit", params={"pat_id": "pat_111"}, headers={"x-api-key": "sk_admin"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    assert {row["path"] for row in data["items"]} == {"/match", "/library"}
    assert all(row["pat_id"] == "pat_111" for row in data["items"])

    r0 = c.get("/v1/audit", params={"pat_id": "pat_missing"}, headers={"x-api-key": "sk_admin"})
    assert r0.status_code == 200 and r0.json()["total"] == 0


def test_filter_by_session_id_and_csv_export_includes_columns(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")
    now = time.time()
    _seed(
        tmp_path / "audit.jsonl",
        [
            {"ts": now - 5, "actor": "key:a", "tenant_id": "acme", "pat_id": "pat_111",
             "session_id": "sess_xyz", "method": "POST", "path": "/match", "status": 200, "roles": ["member"]},
            {"ts": now - 4, "actor": "key:a", "tenant_id": "acme", "pat_id": "pat_111",
             "session_id": "sess_other", "method": "POST", "path": "/match", "status": 200, "roles": ["member"]},
        ],
    )
    r = c.get("/v1/audit", params={"session_id": "sess_xyz"}, headers={"x-api-key": "sk_admin"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["session_id"] == "sess_xyz"

    r2 = c.get(
        "/v1/audit/export",
        params={"format": "csv", "pat_id": "pat_111"},
        headers={"x-api-key": "sk_admin"},
    )
    assert r2.status_code == 200
    reader = csv.DictReader(io.StringIO(r2.text))
    rows = list(reader)
    assert {"pat_id", "session_id"}.issubset(reader.fieldnames or [])
    assert len(rows) == 2
    assert all(row["pat_id"] == "pat_111" for row in rows)


def test_cross_tenant_pat_id_filter_does_not_leak(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_a:9999:admin:acme,ops:sk_b:9999:admin:beta")
    now = time.time()
    _seed(
        tmp_path / "audit.jsonl",
        [
            {"ts": now, "actor": "k", "tenant_id": "beta", "pat_id": "pat_shared",
             "method": "POST", "path": "/match", "status": 200, "roles": ["admin"]},
        ],
    )
    r = c.get("/v1/audit", params={"pat_id": "pat_shared"}, headers={"x-api-key": "sk_a"})
    assert r.status_code == 200
    assert r.json()["total"] == 0
