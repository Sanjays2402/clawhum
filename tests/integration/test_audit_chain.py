"""Tests for the tamper-evident audit log hash chain.

The audit log carries a sha256 hash chain so a procurement reviewer
can prove no entry was edited, deleted, or reordered after the fact.
These tests pin three behaviours:

1. Genuine writes produce a chain that verifies clean and the verify
   endpoint surfaces every file plus a tail digest.
2. Editing a single field on any prior entry breaks that line and
   the verifier flags the first offending line number with a reason.
3. Deleting a line breaks the next line because its prev_hash no
   longer matches the new previous tail digest.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(audit_path))
    monkeypatch.setenv("CLAWHUM_API_KEYS", "ops:sk_admin:9999:admin:acme")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import audit as audit_mod
    audit_mod._reset_chain_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app()), audit_path


def _emit_writes(c: TestClient) -> None:
    # Three POST/PUT/DELETEs is enough to chain a few entries.
    c.post("/feedback", json={"query_id": "q1", "rank": 1, "good": True},
           headers={"X-API-Key": "sk_admin"})
    c.post("/feedback", json={"query_id": "q2", "rank": 1, "good": False},
           headers={"X-API-Key": "sk_admin"})
    c.post("/feedback", json={"query_id": "q3", "rank": 2, "good": True},
           headers={"X-API-Key": "sk_admin"})


def test_chain_verifies_clean_after_normal_writes(monkeypatch, tmp_path):
    c, audit_path = _client(monkeypatch, tmp_path)
    _emit_writes(c)
    assert audit_path.exists()
    r = c.get("/audit/verify", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert len(body["files"]) >= 1
    active = body["files"][0]
    assert active["entries"] >= 3
    assert active["valid"] == active["entries"]
    assert active["first_bad_line"] is None
    assert isinstance(active["tail_entry_hash"], str) and len(active["tail_entry_hash"]) == 64
    # First entry must point at the well-known genesis hash so a
    # truncated head is detectable.
    assert active["head_prev_hash"] == "0" * 64


def test_edited_entry_breaks_chain(monkeypatch, tmp_path):
    c, audit_path = _client(monkeypatch, tmp_path)
    _emit_writes(c)
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    # Edit the second persisted entry's status code field.
    target = json.loads(lines[1])
    target["status"] = 999
    lines[1] = json.dumps(target, separators=(",", ":"), sort_keys=True)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = c.get("/audit/verify", headers={"X-API-Key": "sk_admin"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    active = body["files"][0]
    assert active["first_bad_line"] == 2
    assert active["reason"] is not None


def test_deleted_entry_breaks_chain(monkeypatch, tmp_path):
    c, audit_path = _client(monkeypatch, tmp_path)
    _emit_writes(c)
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3
    # Drop the second entry; the third entry's prev_hash now points at
    # a digest that does not match the new previous tail.
    del lines[1]
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = c.get("/audit/verify", headers={"X-API-Key": "sk_admin"})
    body = r.json()
    assert body["ok"] is False
    active = body["files"][0]
    assert active["first_bad_line"] == 2
    assert "prev_hash mismatch" in (active["reason"] or "")


def test_verify_requires_admin(monkeypatch, tmp_path):
    spec = "acme_ops:sk_admin:9999:admin:acme,acme_user:sk_member:9999:member:acme"
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", spec)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import audit as audit_mod
    audit_mod._reset_chain_cache()
    from clawhum_api.app import create_app
    c = TestClient(create_app())
    blocked = c.get("/audit/verify", headers={"X-API-Key": "sk_member"})
    assert blocked.status_code == 403
    ok = c.get("/audit/verify", headers={"X-API-Key": "sk_admin"})
    assert ok.status_code == 200
