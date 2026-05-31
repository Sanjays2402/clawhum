"""Integration tests for per-workspace closure / wind-down lifecycle."""

from __future__ import annotations

import time

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
    from clawhum_api import closure
    closure.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_status_starts_active(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/workspace/closure", headers={"X-API-Key": "acmereader"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == "acme"
        assert body["state"] == "active"
        assert body["closure"] is None


def test_reader_cannot_schedule_closure(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/workspace/closure",
            json={"reason": "blocked"},
            headers={"X-API-Key": "acmereader"},
        )
        assert r.status_code == 403


def test_schedule_blocks_mutations_then_cancel_restores(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Baseline: a mutation succeeds.
        r = c.post(
            "/collections",
            json={"title": "baseline"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code in (200, 201), r.text

        # Schedule closure with a 7-day grace window.
        r = c.post(
            "/workspace/closure",
            json={"reason": "winding down", "grace_seconds": 7 * 24 * 3600},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        assert r.json()["state"] == "scheduled"

        # Mutations are blocked with 423 and the finalize header.
        r = c.post(
            "/collections",
            json={"title": "during-grace"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 423, r.text
        assert "workspace_closing" in r.json()["detail"]
        assert r.headers.get("X-Workspace-State") == "scheduled"
        assert "X-Workspace-Finalize-At" in r.headers

        # Reads still work during grace.
        r = c.get("/collections", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text

        # Status endpoint stays reachable.
        r = c.get("/workspace/closure", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        assert r.json()["state"] == "scheduled"

        # Cross-tenant isolation: globex is unaffected.
        r = c.post(
            "/collections",
            json={"title": "globex-collection"},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code in (200, 201), r.text
        r = c.get("/workspace/closure", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json()["state"] == "active"

        # Cancel the closure on acme: mutations resume.
        r = c.post(
            f"/workspace/closure/{cid}/cancel",
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "cancelled"

        r = c.post(
            "/collections",
            json={"title": "after-cancel"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code in (200, 201), r.text


def test_finalized_closure_returns_410_on_reads(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Schedule a minimal grace window then wait it out.
        r = c.post(
            "/workspace/closure",
            json={"reason": "drop dead", "grace_seconds": 60},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text

        # Force the in-memory store past the finalize timestamp by
        # mutating the JSONL on disk. We can't time.sleep(60) in a
        # test, so we replace the cached row.
        from clawhum_api import closure as _closure
        # Append a synthetic "scheduled" row with finalize_at in the
        # past for a brand-new tenant key so we can observe the 410.
        import json
        path = _closure._store_path()
        path.write_text(
            json.dumps({
                "kind": "scheduled",
                "id": "synthetic",
                "tenant_id": "acme",
                "reason": "expired",
                "ts": time.time() - 7200,
                "actor": "test",
                "finalize_at": time.time() - 3600,
            }) + "\n",
            encoding="utf-8",
        )
        _closure.reset_cache()

        # A non-export read is now 410 Gone.
        r = c.get("/collections", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 410, r.text
        assert "workspace_closed" in r.json()["detail"]
        assert r.headers.get("X-Workspace-State") == "closed"

        # Audit + closure status remain reachable so the customer can
        # still pull data and inspect the timeline.
        r = c.get("/workspace/closure", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        assert r.json()["state"] == "closed"
