"""HMAC-signed workspace export verification.

Proves end to end:
  * every workspace export ships with a signature header + signature.json
    embedded in the ZIP,
  * the signature verifies via /v1/privacy/workspace-export/verify,
  * tenant B cannot verify tenant A's signature (cross-tenant isolation),
  * tampering with the manifest sha256 makes the signature fail,
  * key admin routes are admin+MFA gated (reader cannot rotate).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path, *, api_keys: str) -> TestClient:
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_EXPORT_SIGNING_KEYS_PATH", str(tmp_path / "export_signing_keys.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import export_signing as _es
    _es.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def _download_export(c: TestClient, key: str):
    r = c.get("/v1/privacy/workspace-export", headers={"x-api-key": key})
    assert r.status_code == 200, r.text
    return r


def test_export_is_signed_and_verifies(monkeypatch, tmp_path):
    spec = "acme_admin:sk_acme:600:admin:acme"
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        c.post(
            "/feedback",
            json={"query_id": "q1", "track_id": "t1", "score": 0.9, "vote": 1},
            headers={"x-api-key": "sk_acme"},
        )
        r = _download_export(c, "sk_acme")
        assert "x-clawhum-export-signature" in r.headers
        assert "x-clawhum-export-key-id" in r.headers
        assert r.headers["x-clawhum-export-signature-alg"] == "HMAC-SHA256"

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            sig_file = json.loads(zf.read("signature.json"))

        assert manifest["signature"]["value"] == r.headers["x-clawhum-export-signature"]
        assert manifest["signature"]["key_id"] == r.headers["x-clawhum-export-key-id"]
        assert sig_file["signature"] == manifest["signature"]["value"]

        # Verify by uploading the full manifest.
        v = c.post(
            "/v1/privacy/workspace-export/verify",
            headers={"x-api-key": "sk_acme"},
            json={"manifest": manifest},
        )
        assert v.status_code == 200, v.text
        body = v.json()
        assert body["valid"] is True
        assert body["tenant_id"] == "acme"
        assert body["is_active_key"] is True

        # Tampered sha256 must fail.
        tampered = dict(manifest)
        tampered["sha256"] = "0" * 64
        v2 = c.post(
            "/v1/privacy/workspace-export/verify",
            headers={"x-api-key": "sk_acme"},
            json={"manifest": tampered},
        )
        assert v2.status_code == 400
        assert v2.json()["valid"] is False


def test_verify_is_tenant_scoped(monkeypatch, tmp_path):
    spec = (
        "acme_admin:sk_acme:600:admin:acme,"
        "globex_admin:sk_globex:600:admin:globex"
    )
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        r = _download_export(c, "sk_acme")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            manifest = json.loads(zf.read("manifest.json"))

        # Globex tries to verify Acme's bundle: rejected because the
        # manifest's tenant_id does not match the caller's tenant.
        v = c.post(
            "/v1/privacy/workspace-export/verify",
            headers={"x-api-key": "sk_globex"},
            json={"manifest": manifest},
        )
        assert v.status_code == 400
        assert v.json()["valid"] is False
        assert "tenant" in v.json()["reason"].lower()

        # Even if Globex strips the tenant_id and signs the discrete
        # fields, their workspace's signing key cannot reproduce the
        # signature so verification still fails.
        v2 = c.post(
            "/v1/privacy/workspace-export/verify",
            headers={"x-api-key": "sk_globex"},
            json={
                "manifest_sha256": manifest["sha256"],
                "generated_at": manifest["generated_at"],
                "key_id": manifest["signature"]["key_id"],
                "signature": manifest["signature"]["value"],
            },
        )
        assert v2.status_code == 400
        assert v2.json()["valid"] is False


def test_signing_key_admin_is_admin_only(monkeypatch, tmp_path):
    spec = (
        "acme_admin:sk_acme:600:admin:acme,"
        "acme_reader:sk_reader:600:reader:acme"
    )
    with _client(monkeypatch, tmp_path, api_keys=spec) as c:
        # Reader can GET (public info only) but not mint/rotate/reveal.
        r = c.get("/export-signing", headers={"x-api-key": "sk_reader"})
        assert r.status_code == 200
        assert r.json()["exists"] is False

        for path in ("/export-signing/mint", "/export-signing/rotate", "/export-signing/reveal"):
            r2 = c.post(path, headers={"x-api-key": "sk_reader"})
            assert r2.status_code in (401, 403), f"{path} returned {r2.status_code}"

        # Admin mint returns the secret exactly once.
        r3 = c.post("/export-signing/mint", headers={"x-api-key": "sk_acme"})
        # MFA may be off in test env; require_admin_with_mfa returns
        # 200 when MFA is not configured for the actor.
        assert r3.status_code == 200, r3.text
        body = r3.json()
        assert body["minted"] is True
        assert body["secret"].startswith("esk_")
        first_key_id = body["key_id"]

        # Re-minting is a no-op.
        r4 = c.post("/export-signing/mint", headers={"x-api-key": "sk_acme"})
        assert r4.status_code == 200
        assert r4.json()["minted"] is False
        assert "secret" not in r4.json()

        # Rotate produces a new key id and a new secret.
        r5 = c.post("/export-signing/rotate", headers={"x-api-key": "sk_acme"})
        assert r5.status_code == 200, r5.text
        rb = r5.json()
        assert rb["rotated"] is True
        assert rb["key_id"] != first_key_id
        assert rb["secret"] != body["secret"]
