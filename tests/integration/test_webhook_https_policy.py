"""Per-workspace HTTPS-only webhook policy enforcement tests.

What an enterprise security review checks here:

1. With no policy, plaintext http:// registrations still work (opt-in feature).
2. With the policy on, POST /webhooks for http:// fails with HTTP 400 and a
   machine-parseable {code: "webhook_https_required"} body.
3. With the policy on, https:// registration still works.
4. Tenant A turning the policy on has zero effect on tenant B
   (no cross-tenant policy leakage).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_WEBHOOKS_PATH", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_DELIVERIES_PATH", str(tmp_path / "webhook_deliveries.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_WEBHOOK_POLICY_PATH", str(tmp_path / "webhook_policy.jsonl")
    )
    # Allow public hosts in tests; default safety policy is fine.
    monkeypatch.setenv("CLAWHUM_WEBHOOK_BLOCK_PRIVATE_IPS", "false")
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED", "false")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api import webhook_policy

    webhook_policy.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_default_policy_allows_plaintext(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/webhook-policy", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["require_https"] is False
        assert body["plaintext_endpoint_count"] == 0

        r = c.post(
            "/webhooks",
            json={
                "url": "http://example.com/hook",
                "events": ["match.completed"],
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_policy_on_blocks_plaintext_with_structured_code(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.put(
            "/webhook-policy",
            json={"require_https": True},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["require_https"] is True

        r = c.post(
            "/webhooks",
            json={
                "url": "http://example.com/hook",
                "events": ["match.completed"],
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "webhook_https_required"

        # https still works
        r = c.post(
            "/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["match.completed"],
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text


def test_no_cross_tenant_leakage(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme requires https
        c.put(
            "/webhook-policy",
            json={"require_https": True},
            headers={"X-API-Key": "acmekey"},
        )
        # Globex must NOT see acme's policy
        r = c.get("/webhook-policy", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        assert r.json()["require_https"] is False

        # Globex can still register plaintext
        r = c.post(
            "/webhooks",
            json={
                "url": "http://example.com/hook",
                "events": ["match.completed"],
            },
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 200, r.text


def test_plaintext_count_warns_before_flip(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Register two plaintext endpoints while policy is off
        for _ in range(2):
            r = c.post(
                "/webhooks",
                json={
                    "url": "http://example.com/hook",
                    "events": ["match.completed"],
                },
                headers={"X-API-Key": "acmekey"},
            )
            assert r.status_code == 200, r.text

        r = c.get("/webhook-policy", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        assert r.json()["plaintext_endpoint_count"] == 2


def test_min_tls_version_round_trip_and_validation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Default is no floor.
        r = c.get("/webhook-policy", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["min_tls_version"] == ""
        assert "1.2" in body["allowed_min_tls_versions"]
        assert "1.3" in body["allowed_min_tls_versions"]

        # Set TLS 1.2 floor; it implicitly requires https.
        r = c.put(
            "/webhook-policy",
            json={"require_https": False, "min_tls_version": "1.2"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        # Plaintext URLs are now rejected because the TLS floor implies https.
        r = c.post(
            "/webhooks",
            json={
                "url": "http://example.com/hook",
                "events": ["match.completed"],
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "webhook_https_required"

        # https still works
        r = c.post(
            "/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["match.completed"],
            },
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text

        # Invalid TLS version is rejected with a structured code.
        r = c.put(
            "/webhook-policy",
            json={"require_https": True, "min_tls_version": "1.1"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "webhook_policy_invalid"


def test_min_tls_version_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme pins TLS 1.3
        r = c.put(
            "/webhook-policy",
            json={"require_https": True, "min_tls_version": "1.3"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        # Globex must NOT see acme's TLS floor.
        r = c.get("/webhook-policy", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200, r.text
        gb = r.json()
        assert gb["min_tls_version"] == ""
        assert gb["require_https"] is False


def test_build_ssl_context_pins_minimum_version(monkeypatch, tmp_path):
    # No HTTP needed: validate the SSLContext builder used by the
    # delivery worker so a floor actually translates to an OS-level pin.
    import ssl
    from clawhum_api import webhook_policy

    assert webhook_policy.build_ssl_context("") is None
    ctx12 = webhook_policy.build_ssl_context("1.2")
    assert ctx12 is not None
    assert ctx12.minimum_version == ssl.TLSVersion.TLSv1_2
    ctx13 = webhook_policy.build_ssl_context("1.3")
    assert ctx13 is not None
    assert ctx13.minimum_version == ssl.TLSVersion.TLSv1_3
