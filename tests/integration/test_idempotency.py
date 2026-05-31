"""Tests for Idempotency-Key replay middleware.

These tests pin four behaviours that enterprise integrators rely on
when they retry mutating requests after a network blip:

1. A POST repeated with the same Idempotency-Key returns the original
   response body byte-for-byte and tags the reply with
   ``Idempotent-Replayed: true``.
2. Reusing the key with a different body returns HTTP 409 so a buggy
   client cannot silently overwrite the original outcome.
3. Two tenants can use the same key string without collision because
   the cache is scoped per caller.
4. Missing or malformed keys do not break normal traffic; the
   middleware is fully opt-in.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, tenant_keys: str | None = None):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        tenant_keys
        or "alpha:sk_alpha:9999:admin:acme,beta:sk_beta:9999:admin:beta",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_IDEMPOTENCY_ENABLED", "true")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import idempotency as idem
    idem._registered.clear()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_replay_returns_cached_body_and_marks_replayed(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    body = {"query_id": "q1", "track_id": "t1", "score": 0.91, "vote": 1}
    h = {"X-API-Key": "sk_alpha", "Idempotency-Key": "feedback-001"}

    first = c.post("/feedback", json=body, headers=h)
    assert first.status_code in (200, 201, 204), first.text
    assert first.headers.get("Idempotent-Replayed") == "false"
    first_body = first.content
    first_request_id = first.headers.get("x-request-id")

    second = c.post("/feedback", json=body, headers=h)
    assert second.status_code == first.status_code
    assert second.content == first_body
    assert second.headers.get("Idempotent-Replayed") == "true"
    # Replay surfaces the original request id under a dedicated
    # header so log searches can stitch the retry to the origin call.
    assert second.headers.get("x-original-request-id") == first_request_id


def test_same_key_different_body_returns_409(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    h = {"X-API-Key": "sk_alpha", "Idempotency-Key": "feedback-002"}

    first = c.post(
        "/feedback",
        json={"query_id": "q1", "track_id": "t1", "score": 0.9, "vote": 1},
        headers=h,
    )
    assert first.status_code in (200, 201, 204)

    conflict = c.post(
        "/feedback",
        json={"query_id": "q1", "track_id": "t1", "score": 0.9, "vote": -1},  # flipped vote
        headers=h,
    )
    assert conflict.status_code == 409
    payload = conflict.json()
    assert payload["error"] == "idempotency_key_conflict"


def test_keys_are_isolated_per_tenant(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    key = "shared-key-value"

    alpha = c.post(
        "/feedback",
        json={"query_id": "qA", "track_id": "tA", "score": 0.8, "vote": 1},
        headers={"X-API-Key": "sk_alpha", "Idempotency-Key": key},
    )
    assert alpha.status_code in (200, 201, 204), alpha.text

    # Beta uses the SAME key string with a DIFFERENT body. If the
    # cache leaked across tenants this would either 409 or replay
    # alpha's body. Neither is acceptable, so both must be false.
    beta = c.post(
        "/feedback",
        json={"query_id": "qB", "track_id": "tB", "score": 0.7, "vote": -1},
        headers={"X-API-Key": "sk_beta", "Idempotency-Key": key},
    )
    assert beta.status_code in (200, 201, 204), beta.text
    assert beta.headers.get("Idempotent-Replayed") == "false"
    assert beta.content != alpha.content or alpha.status_code == 204


def test_no_key_is_passthrough(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post(
        "/feedback",
        json={"query_id": "qX", "track_id": "tX", "score": 0.5, "vote": 0},
        headers={"X-API-Key": "sk_alpha"},
    )
    assert r.status_code in (200, 201, 204)
    # No header should be emitted when the client did not opt in.
    assert "Idempotent-Replayed" not in r.headers


def test_malformed_key_returns_400(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post(
        "/feedback",
        json={"query_id": "qX", "track_id": "tX", "score": 0.5, "vote": 0},
        headers={"X-API-Key": "sk_alpha", "Idempotency-Key": "bad key with spaces"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_idempotency_key"
