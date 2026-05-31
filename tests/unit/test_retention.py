"""Unit tests for the workspace data retention module.

Exercises the storage, read-time filter, and enforcement sweep
without booting the full FastAPI app. Cross-tenant isolation is the
key procurement guarantee here: tenant A's policy and sweep must
never touch tenant B's rows.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def _reset(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "hist.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "fb.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_IP_ALLOWLIST_PATH", str(tmp_path / "ipa.jsonl"))
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api import retention
    retention.reset_cache()
    return retention


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_default_policy_is_empty(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    pol = retention.get_policy("acme")
    assert pol.is_empty()
    assert pol.history_days == 0


def test_set_policy_persists_and_invalidates_cache(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    retention.set_policy("acme", history_days=7, audit_days=30, updated_by="key:abc")
    pol = retention.get_policy("acme")
    assert pol.history_days == 7
    assert pol.audit_days == 30
    assert pol.feedback_days == 0
    # Updates rewrite, do not append duplicates.
    retention.set_policy("acme", history_days=14)
    pol2 = retention.get_policy("acme")
    assert pol2.history_days == 14
    assert pol2.audit_days == 0  # unset fields reset to zero


def test_filter_expired_hides_old_tenant_rows(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    retention.set_policy("acme", history_days=7)
    now = time.time()
    rows = [
        {"id": "1", "tenant_id": "acme", "created_at": now - (40 * 86400)},
        {"id": "2", "tenant_id": "acme", "created_at": now - (1 * 86400)},
        {"id": "3", "tenant_id": "acme"},  # no timestamp, treated as fresh
        {"id": "4", "tenant_id": "globex", "created_at": now - (40 * 86400)},
    ]
    out = retention.filter_expired(rows, "history", "acme", now=now)
    ids = {r["id"] for r in out}
    # acme's old row gone; acme's new + untimed remain; other tenant untouched.
    assert ids == {"2", "3", "4"}


def test_enforce_policy_only_touches_caller_rows(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    hist = tmp_path / "hist.jsonl"
    now = time.time()
    _write_jsonl(
        hist,
        [
            {"id": "a_old", "tenant_id": "acme", "created_at": now - (40 * 86400)},
            {"id": "a_new", "tenant_id": "acme", "created_at": now - (1 * 86400)},
            {"id": "g_old", "tenant_id": "globex", "created_at": now - (40 * 86400)},
        ],
    )
    retention.set_policy("acme", history_days=7)
    removed = retention.enforce_policy("acme", now=now)
    assert removed["history"] == 1
    surviving = [json.loads(l) for l in hist.read_text().splitlines() if l.strip()]
    ids = {r["id"] for r in surviving}
    # Cross-tenant guarantee: globex's row is untouched.
    assert ids == {"a_new", "g_old"}


def test_enforce_with_empty_policy_is_noop(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    hist = tmp_path / "hist.jsonl"
    now = time.time()
    _write_jsonl(hist, [{"id": "x", "tenant_id": "acme", "created_at": now - (999 * 86400)}])
    removed = retention.enforce_policy("acme", now=now)
    assert all(v == 0 for v in removed.values())
    assert "x" in hist.read_text()


def test_set_policy_rejects_empty_tenant(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        retention.set_policy("", history_days=7)


def test_filter_expired_rejects_unknown_category(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        retention.filter_expired([], "not_a_category", "acme")


def test_policies_isolated_per_tenant(monkeypatch, tmp_path):
    retention = _reset(monkeypatch, tmp_path)
    retention.set_policy("acme", history_days=7)
    retention.set_policy("globex", history_days=90)
    assert retention.get_policy("acme").history_days == 7
    assert retention.get_policy("globex").history_days == 90
    # Neither tenant sees the other's policy as their own.
    assert retention.get_policy("nobody").is_empty()
