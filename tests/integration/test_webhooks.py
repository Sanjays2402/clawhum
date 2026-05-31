from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, open_auth: bool = False):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "webhook_deliveries.jsonl")
    )
    if open_auth:
        monkeypatch.delenv("CLAWHUM_API_KEYS", raising=False)
        monkeypatch.setenv("CLAWHUM_API_KEY", "")
    else:
        monkeypatch.setenv("CLAWHUM_API_KEYS", "main:changeme:10000:writer")
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_create_list_delete_webhook(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # unauth blocked
        assert c.post("/webhooks", json={"url": "https://example.com/hook"}).status_code == 401

        r = c.post(
            "/webhooks",
            json={"url": "https://example.com/hook", "events": ["match.completed"]},
            headers={"X-API-Key": "changeme"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] and len(body["id"]) == 12
        assert body["secret"].startswith("whsec_")
        assert body["events"] == ["match.completed"]

        # list shows it without the secret
        r2 = c.get("/webhooks", headers={"X-API-Key": "changeme"})
        assert r2.status_code == 200
        items = r2.json()["webhooks"]
        assert len(items) == 1
        assert items[0]["id"] == body["id"]
        assert "secret" not in items[0]
        assert items[0]["secret_hint"]

        # invalid event rejected
        bad = c.post(
            "/webhooks",
            json={"url": "https://example.com/h", "events": ["nope"]},
            headers={"X-API-Key": "changeme"},
        )
        assert bad.status_code == 400

        # delete
        d = c.delete(f"/webhooks/{body['id']}", headers={"X-API-Key": "changeme"})
        assert d.status_code == 200
        assert d.json()["ok"] is True

        # gone
        r3 = c.get("/webhooks", headers={"X-API-Key": "changeme"})
        assert r3.json()["webhooks"] == []

        # deleting again is 404
        d2 = c.delete(f"/webhooks/{body['id']}", headers={"X-API-Key": "changeme"})
        assert d2.status_code == 404


def test_signature_helper_matches_hmac():
    from clawhum_api.routes.webhooks import sign_body

    body = b'{"hello":"world"}'
    secret = "whsec_demo"
    got = sign_body(secret, body)
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(got, expected)


def test_deliveries_scoped_to_owner(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/webhooks",
            json={"url": "https://example.com/hook"},
            headers={"X-API-Key": "changeme"},
        )
        assert r.status_code == 200
        hook_id = r.json()["id"]

        # empty log is fine, but the path must respond.
        r2 = c.get(
            f"/webhooks/{hook_id}/deliveries", headers={"X-API-Key": "changeme"}
        )
        assert r2.status_code == 200
        assert r2.json()["deliveries"] == []

        # unknown id (different tenant / never existed) -> 404
        r3 = c.get("/webhooks/abcdef012345/deliveries", headers={"X-API-Key": "changeme"})
        assert r3.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_writes_delivery_log_on_failure(monkeypatch, tmp_path):
    # Point a webhook at a URL that does not resolve. We expect the dispatcher
    # to record an attempt with ok=false and a non-empty error.
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "webhook_deliveries.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_WEBHOOK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("CLAWHUM_WEBHOOK_TIMEOUT_SEC", "1.0")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()

    from clawhum_api.routes import webhooks as wh

    rec = {
        "id": "deadbeef0001",
        "tenant_id": "tenant-x",
        "url": "http://127.0.0.1:1/never",
        "events": ["match.completed"],
        "created_at": 0.0,
        "active": True,
        "secret_hash": "x",
        "secret_hint": "x",
    }
    wh._append_hook(rec)

    n = await wh.dispatch_event(
        "tenant-x", "match.completed", {"hello": "world"}
    )
    assert n == 1

    # Give the background task a moment to write its row.
    import asyncio as _aio

    for _ in range(50):
        await _aio.sleep(0.05)
        rows = list(wh._iter_jsonl(wh._deliveries_path()))
        if rows:
            break
    rows = list(wh._iter_jsonl(wh._deliveries_path()))
    assert rows, "expected at least one delivery row"
    assert rows[0]["webhook_id"] == "deadbeef0001"
    assert rows[0]["ok"] is False
    assert rows[0]["error"]


def test_webhook_test_endpoint_fires_real_request(monkeypatch, tmp_path):
    """The /test endpoint must perform a real HTTP POST and log the result."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            received["body"] = self.rfile.read(length)
            received["event"] = self.headers.get("X-Clawhum-Event")
            received["hint"] = self.headers.get("X-Clawhum-Signature-Hint")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_a, **_kw):  # silence
            return

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with _client(monkeypatch, tmp_path) as c:
            r = c.post(
                "/webhooks",
                json={"url": f"http://127.0.0.1:{port}/hook"},
                headers={"X-API-Key": "changeme"},
            )
            assert r.status_code == 200, r.text
            hook_id = r.json()["id"]

            # Unauthenticated should be blocked.
            assert c.post(f"/webhooks/{hook_id}/test").status_code == 401

            r2 = c.post(
                f"/webhooks/{hook_id}/test",
                headers={"X-API-Key": "changeme"},
            )
            assert r2.status_code == 200, r2.text
            body = r2.json()
            assert body["ok"] is True
            assert body["event"] == "webhook.test"
            assert body["delivery_id"]

            assert received.get("event") == "webhook.test"
            assert b"clawhum webhook test ping" in (received.get("body") or b"")

            # Delivery log shows the test attempt.
            log = c.get(
                f"/webhooks/{hook_id}/deliveries",
                headers={"X-API-Key": "changeme"},
            ).json()["deliveries"]
            assert any(d["event"] == "webhook.test" and d["ok"] for d in log)

            # Unknown hook id is 404.
            r3 = c.post(
                "/webhooks/zzzzzzzzzzzz/test",
                headers={"X-API-Key": "changeme"},
            )
            assert r3.status_code == 404

            # A test delivery has no stored payload so redeliver returns 422.
            test_delivery = next(d for d in log if d["event"] == "webhook.test")
            assert test_delivery["replayable"] is False
            r4 = c.post(
                f"/webhooks/{hook_id}/deliveries/{test_delivery['id']}/redeliver",
                headers={"X-API-Key": "changeme"},
            )
            assert r4.status_code == 422
    finally:
        srv.shutdown()
        srv.server_close()
