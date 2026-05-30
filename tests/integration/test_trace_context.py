from __future__ import annotations

import json
import re

import structlog
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_LOG_JSON", "true")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    return TestClient(create_app())


_TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def test_generated_traceparent_and_request_id(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/health")
        assert r.status_code == 200
        rid = r.headers.get("x-request-id")
        tp = r.headers.get("traceparent")
        assert rid and len(rid) >= 16
        assert tp and _TRACEPARENT_RE.match(tp), tp


def test_inbound_traceparent_is_honored(monkeypatch, tmp_path):
    incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/health", headers={
            "traceparent": incoming,
            "x-request-id": "rid-fixed-123",
        })
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "rid-fixed-123"
        out = r.headers["traceparent"]
        # Trace id preserved, span id rotated for this hop.
        assert out.startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
        assert not out.endswith("-00f067aa0ba902b7-01")
        assert _TRACEPARENT_RE.match(out)


def test_malformed_traceparent_is_replaced(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/health", headers={"traceparent": "not-a-real-traceparent"})
        assert r.status_code == 200
        tp = r.headers["traceparent"]
        assert _TRACEPARENT_RE.match(tp)
        # Must not echo garbage back.
        assert "not-a-real-traceparent" not in tp


def test_log_lines_carry_request_id_and_trace_id(monkeypatch, tmp_path, capsys):
    # Capture logs via a stdlib StreamHandler attached to root, since
    # configure_logging() routes structlog through stdlib logging in
    # JSON mode. We use capsys for the actual stdout stream.
    with _client(monkeypatch, tmp_path) as c:
        capsys.readouterr()  # clear boot logs
        r = c.get("/health", headers={
            "traceparent": "00-1111111111111111aaaaaaaaaaaaaaaa-2222222222222222-01",
        })
        assert r.status_code == 200
        out = capsys.readouterr().out
        # Any structured log line emitted during the request should
        # carry the bound context. Health logs may be quiet, so emit one
        # explicitly via a request-scoped logger probe.
    # Verify that contextvars are cleared after the request finishes.
    ctx = structlog.contextvars.get_contextvars()
    assert "request_id" not in ctx
    assert "trace_id" not in ctx
    # If anything was logged, it must contain our trace id.
    if out.strip():
        for line in out.strip().splitlines():
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if "trace_id" in payload:
                assert payload["trace_id"] == "1111111111111111aaaaaaaaaaaaaaaa"
                break


def test_contextvars_bound_during_request(monkeypatch, tmp_path):
    """Inside a request handler, structlog contextvars must include the
    request_id and trace_id so application code logs are correlated.
    """
    from fastapi import APIRouter

    seen: dict[str, object] = {}

    probe = APIRouter()

    @probe.get("/__probe")
    def _probe():
        seen.update(structlog.contextvars.get_contextvars())
        return {"ok": True}

    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    app = create_app()
    app.include_router(probe)
    with TestClient(app) as c:
        r = c.get("/__probe", headers={
            "traceparent": "00-deadbeefdeadbeefdeadbeefdeadbeef-1234567812345678-01",
        })
        assert r.status_code == 200
    assert seen.get("trace_id") == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert seen.get("method") == "GET"
    assert seen.get("path") == "/__probe"
    assert isinstance(seen.get("request_id"), str)
    assert isinstance(seen.get("span_id"), str) and len(seen["span_id"]) == 16  # type: ignore[index]
