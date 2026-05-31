"""Sandbox / dry-run mode for destructive endpoints.

Every supported DELETE endpoint must, when called with ``?dry_run=true``:
  1. Run the full auth + tenant + permission stack.
  2. Return a structured preview without mutating storage.
  3. Refuse to leak across tenants (a dry-run targeting another tenant's
     resource still returns 404, never a preview).

This file exercises the contract end-to-end against a real app, not a
mock, so half-implemented dry-run support shows up immediately.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "alpha:alpha-secret:10000:writer:tenant-a,"
        "beta:beta-secret:10000:writer:tenant-b",
    )
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _hist_body(name="hum"):
    return {
        "query_id": "q-1",
        "elapsed_ms": 17,
        "count": 1,
        "results": [
            {"track_id": "t1", "title": "Yesterday", "artist": "The Beatles", "score": 0.8},
        ],
        "filename": "rec.wav",
        "duration_sec": 4.2,
        "name": name,
        "tags": [],
    }


def test_history_delete_dry_run_does_not_mutate(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "alpha-secret"}
        r = c.post("/history", json=_hist_body(), headers=h)
        assert r.status_code == 200, r.text
        hid = r.json()["id"]

        # Dry-run preview returns the standard envelope.
        preview = c.delete(f"/history/{hid}?dry_run=true", headers=h)
        assert preview.status_code == 200
        body = preview.json()
        assert body["dry_run"] is True
        assert body["would_delete"]["kind"] == "history"
        assert body["would_delete"]["id"] == hid
        assert body["tenant_id"] == "tenant-a"

        # And the row is still there.
        listing = c.get("/history", headers=h).json()
        assert any(item["id"] == hid for item in listing["items"])

        # Real delete then succeeds.
        real = c.delete(f"/history/{hid}", headers=h)
        assert real.status_code == 200
        listing2 = c.get("/history", headers=h).json()
        assert not any(item["id"] == hid for item in listing2["items"])


def test_dry_run_respects_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        alpha = {"X-API-Key": "alpha-secret"}
        beta = {"X-API-Key": "beta-secret"}

        r = c.post("/history", json=_hist_body("alpha-only"), headers=alpha)
        assert r.status_code == 200
        hid = r.json()["id"]

        # Cross-tenant dry-run must NOT reveal the resource exists.
        cross = c.delete(f"/history/{hid}?dry_run=true", headers=beta)
        assert cross.status_code == 404

        # And nothing is mutated for the rightful owner.
        listing = c.get("/history", headers=alpha).json()
        assert any(item["id"] == hid for item in listing["items"])


def test_dry_run_header_form_also_works(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        h = {"X-API-Key": "alpha-secret"}
        r = c.post("/history", json=_hist_body(), headers=h)
        hid = r.json()["id"]

        preview = c.delete(
            f"/history/{hid}",
            headers={**h, "X-Dry-Run": "1"},
        )
        assert preview.status_code == 200
        assert preview.json()["dry_run"] is True
