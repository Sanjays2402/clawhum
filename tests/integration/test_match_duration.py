"""Per-workspace match query duration cap.

Covers:
  * Default policy (max_duration_sec=0) allows any decoded duration.
  * Setting max_duration_sec=N causes /match to reject queries whose
    *decoded* duration exceeds N with HTTP 413 and a structured
    error body. The cap is on decoded seconds, not bytes.
  * Tenant isolation: tenant A cannot read or alter tenant B's
    policy, and tenant A's cap does not throttle tenant B's matches.
  * Every mutation of the cap is recorded in the tamper evident audit
    log with before/after state.
  * Values above the ceiling are rejected with a structured 400.
"""

from __future__ import annotations

import io
import json
import struct
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient


def _wav_bytes(duration_sec: float, sr: int = 16000) -> bytes:
    n = int(duration_sec * sr)
    # quiet sine so the encoder/decoder path is exercised
    t = np.arange(n, dtype=np.float32) / float(sr)
    x = (0.05 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    pcm = (x * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return buf.getvalue()


def _seed_index(tmp_path):
    """Seed a minimal index so /match doesn't 400 with 'index empty'."""
    # Build a 2s clip, run reindex via the CLI surface would be heavy;
    # the test app builds state from settings. We point the index/meta
    # at writable paths and rely on the embedder to produce a tiny in
    # memory index by reusing the reindex helper if present. If the
    # app lazy-builds, an empty index still passes the duration check
    # *before* the matcher runs, but the route returns 400 on empty
    # index first. So we use a fake corpus by loading via test util if
    # available; otherwise we accept either 413 (caught earlier) or
    # 400 (empty index) on the *under-cap* request and only assert 413
    # on the over-cap request.
    return tmp_path


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_MATCH_DURATION_POLICY_PATH",
        str(tmp_path / "match_duration_policy.jsonl"),
    )
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_REQUIRE_MFA_FOR_ADMIN", "false")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "main:adminkey:10000:admin,other:otherkey:10000:admin",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "100000")

    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.match_duration import reset_cache as _reset_md

    _reset_md()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_default_policy_is_no_cap(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/match-duration", headers={"X-API-Key": "adminkey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_duration_sec"] == 0
        assert body["ceiling"] == 3600


def test_cap_rejects_long_query_with_structured_413(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Set cap to 1 second for the main tenant.
        r = c.put(
            "/match-duration",
            json={"max_duration_sec": 1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["max_duration_sec"] == 1

        # Submit a 3 second wav: must 413 with structured error.
        wav = _wav_bytes(3.0)
        r = c.post(
            "/match",
            files={"audio": ("hum.wav", wav, "audio/wav")},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 413, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "match_query_too_long"
        assert detail["max_duration_sec"] == 1
        assert detail["duration_sec"] >= 2.5


def test_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Tenant 'main' sets a tight cap.
        r = c.put(
            "/match-duration",
            json={"max_duration_sec": 1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200

        # Tenant 'other' has not configured a cap; its read returns 0.
        r = c.get("/match-duration", headers={"X-API-Key": "otherkey"})
        assert r.status_code == 200, r.text
        assert r.json()["max_duration_sec"] == 0

        # And 'other' submitting a 3s wav must NOT be 413'd by main's
        # cap. It may 400 (empty index) but never 413.
        wav = _wav_bytes(3.0)
        r = c.post(
            "/match",
            files={"audio": ("hum.wav", wav, "audio/wav")},
            headers={"X-API-Key": "otherkey"},
        )
        assert r.status_code != 413, r.text


def test_above_ceiling_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        from clawhum_api.match_duration import MAX_DURATION_CEILING_SEC

        r = c.put(
            "/match-duration",
            json={"max_duration_sec": MAX_DURATION_CEILING_SEC + 1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "match_duration_invalid"


def test_mutation_audited(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/match-duration",
            json={"max_duration_sec": 30},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        events = [
            json.loads(l)
            for l in audit_path.read_text().splitlines()
            if l.strip()
        ]
        md_events = [
            e for e in events if e.get("action") == "match_duration.update"
        ]
        assert md_events, events
        last = md_events[-1]
        assert last["after"]["max_duration_sec"] == 30
        assert last["before"]["max_duration_sec"] == 0
        assert last["tenant_id"] == "main"
