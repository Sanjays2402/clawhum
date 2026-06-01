"""Workspace data classification policy.

Proves:
  * default level is 'internal' and self-describing read works for admins,
  * setting a level to 'restricted' blocks the workspace-wide export
    until the caller resends with the X-Classification-Ack header,
  * the ack value must exactly match the workspace level,
  * once acknowledged, the JSON export response includes the
    classification payload and the X-Data-Classification headers,
  * non-admin roles cannot read or change the classification.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setenv("CLAWHUM_CLASSIFICATION_PATH", str(tmp_path / "classification.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import classification_store
    classification_store.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_default_classification_is_internal(monkeypatch, tmp_path):
    spec = "acme_admin:sk_acme:600:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        r = c.get("/v1/classification", headers={"x-api-key": "sk_acme"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["classification"]["level"] == "internal"
        assert body["requires_ack"] is False
        assert "restricted" in body["available_levels"]


def test_non_admin_cannot_read_or_write(monkeypatch, tmp_path):
    spec = "acme_member:sk_member:600:member:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        r = c.get("/v1/classification", headers={"x-api-key": "sk_member"})
        assert r.status_code in (401, 403)
        r = c.put(
            "/v1/classification",
            json={"level": "restricted"},
            headers={"x-api-key": "sk_member"},
        )
        assert r.status_code in (401, 403)


def test_restricted_level_blocks_export_until_acked(monkeypatch, tmp_path):
    spec = "acme_admin:sk_acme:600:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        # Promote workspace to restricted.
        r = c.put(
            "/v1/classification",
            json={
                "level": "restricted",
                "label": "PII - EU customers",
                "handling_contact": "dpo@acme.example",
            },
            headers={"x-api-key": "sk_acme"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["level"] == "restricted"

        # Workspace export without ack: blocked with 428 Precondition Required.
        blocked = c.get(
            "/v1/privacy/workspace-export?format=json",
            headers={"x-api-key": "sk_acme"},
        )
        assert blocked.status_code == 428, blocked.text
        detail = blocked.json()["detail"]
        assert detail["error"] == "classification_ack_required"
        assert detail["classification"]["level"] == "restricted"
        assert blocked.headers.get("X-Data-Classification") == "restricted"

        # Wrong ack value: still blocked.
        wrong = c.get(
            "/v1/privacy/workspace-export?format=json",
            headers={"x-api-key": "sk_acme", "X-Classification-Ack": "internal"},
        )
        assert wrong.status_code == 428

        # Correct ack: allowed, response carries classification payload.
        ok = c.get(
            "/v1/privacy/workspace-export?format=json",
            headers={"x-api-key": "sk_acme", "X-Classification-Ack": "restricted"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["classification"]["level"] == "restricted"
        assert body["classification"]["label"] == "PII - EU customers"
        assert ok.headers.get("X-Data-Classification") == "restricted"


def test_internal_level_does_not_require_ack(monkeypatch, tmp_path):
    spec = "acme_admin:sk_acme:600:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        r = c.get(
            "/v1/privacy/workspace-export?format=json",
            headers={"x-api-key": "sk_acme"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["classification"]["level"] == "internal"
