"""Per-workspace match result top_k cap.

Covers:
  * Default policy (max_top_k=0) allows any top_k value.
  * Setting max_top_k=N causes /match to reject requests whose top_k
    exceeds N with HTTP 400 and a structured error body.
  * Tenant isolation: tenant A cannot read or alter tenant B's
    policy, and tenant A's cap does not throttle tenant B's matches.
  * Every mutation of the cap is recorded in the tamper evident audit
    log with before/after state.
  * Values above the ceiling are rejected with a structured 400.
"""

from __future__ import annotations

import io
import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient


def _wav_bytes(duration_sec: float, sr: int = 16000) -> bytes:
    n = int(duration_sec * sr)
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


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_MATCH_TOPK_POLICY_PATH",
        str(tmp_path / "match_topk_policy.jsonl"),
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
    from clawhum_api.match_topk import reset_cache as _reset_mk

    _reset_mk()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_default_policy_is_no_cap(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/match-topk", headers={"X-API-Key": "adminkey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_top_k"] == 0
        assert body["ceiling"] == 1000


def test_cap_rejects_large_topk_with_structured_400(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/match-topk",
            json={"max_top_k": 5},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["max_top_k"] == 5

        wav = _wav_bytes(1.0)
        r = c.post(
            "/match",
            data={"top_k": "50"},
            files={"audio": ("hum.wav", wav, "audio/wav")},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "match_top_k_too_large"
        assert detail["max_top_k"] == 5
        assert detail["top_k"] == 50


def test_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/match-topk",
            json={"max_top_k": 5},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 200

        r = c.get("/match-topk", headers={"X-API-Key": "otherkey"})
        assert r.status_code == 200, r.text
        assert r.json()["max_top_k"] == 0

        # tenant 'other' submitting top_k=50 must NOT be blocked by
        # main's cap. The request may 400 with index-empty, but never
        # with match_top_k_too_large.
        wav = _wav_bytes(1.0)
        r = c.post(
            "/match",
            data={"top_k": "50"},
            files={"audio": ("hum.wav", wav, "audio/wav")},
            headers={"X-API-Key": "otherkey"},
        )
        if r.status_code == 400:
            detail = r.json().get("detail")
            if isinstance(detail, dict):
                assert detail.get("code") != "match_top_k_too_large", r.text


def test_above_ceiling_rejected(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        from clawhum_api.match_topk import MAX_TOP_K_CEILING

        r = c.put(
            "/match-topk",
            json={"max_top_k": MAX_TOP_K_CEILING + 1},
            headers={"X-API-Key": "adminkey"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "match_topk_invalid"


def test_mutation_audited(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/match-topk",
            json={"max_top_k": 25},
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
            e for e in events if e.get("action") == "match_topk.update"
        ]
        assert md_events, events
        last = md_events[-1]
        assert last["after"]["max_top_k"] == 25
        assert last["before"]["max_top_k"] == 0
        assert last["tenant_id"] == "main"
