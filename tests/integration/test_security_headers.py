from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "changeme")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def test_default_security_headers_present(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/health")
        assert r.status_code == 200
        h = r.headers
        assert h.get("X-Content-Type-Options") == "nosniff"
        assert h.get("X-Frame-Options") == "DENY"
        assert h.get("Referrer-Policy") == "no-referrer"
        assert "geolocation=()" in h.get("Permissions-Policy", "")
        assert "default-src 'none'" in h.get("Content-Security-Policy", "")
        assert h.get("Cross-Origin-Opener-Policy") == "same-origin"
        assert h.get("Cross-Origin-Resource-Policy") == "same-origin"


def test_hsts_only_on_https(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # TestClient defaults to http, so no HSTS
        r = c.get("/health")
        assert "Strict-Transport-Security" not in r.headers
        # Simulate a TLS terminating proxy.
        r2 = c.get("/health", headers={"X-Forwarded-Proto": "https"})
        hsts = r2.headers.get("Strict-Transport-Security", "")
        assert hsts.startswith("max-age=")
        assert "includeSubDomains" in hsts


def test_security_headers_can_be_disabled(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, CLAWHUM_SECURITY_HEADERS_ENABLED="false") as c:
        r = c.get("/health")
        assert "X-Frame-Options" not in r.headers
        assert "Content-Security-Policy" not in r.headers


def test_cors_default_wildcard_blocks_credentials(monkeypatch, tmp_path):
    # Default wildcard must not advertise credentialed CORS (browsers reject it).
    with _client(
        monkeypatch,
        tmp_path,
        CLAWHUM_CORS_ALLOW_CREDENTIALS="true",
    ) as c:
        r = c.options(
            "/health",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Starlette returns 200 for valid preflight.
        assert r.status_code in (200, 204)
        assert r.headers.get("Access-Control-Allow-Credentials") != "true"


def test_cors_explicit_origins_allow_credentials(monkeypatch, tmp_path):
    with _client(
        monkeypatch,
        tmp_path,
        CLAWHUM_CORS_ALLOW_ORIGINS="https://app.example.com,https://admin.example.com",
        CLAWHUM_CORS_ALLOW_CREDENTIALS="true",
    ) as c:
        r = c.options(
            "/health",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert r.status_code in (200, 204)
        assert r.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"
        assert r.headers.get("Access-Control-Allow-Credentials") == "true"

    # A disallowed origin must not be echoed back.
    with _client(
        monkeypatch,
        tmp_path,
        CLAWHUM_CORS_ALLOW_ORIGINS="https://app.example.com",
    ) as c:
        r = c.options(
            "/health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Starlette returns 400 for disallowed preflight in newer versions,
        # but in all versions it must not echo the bad origin.
        assert r.headers.get("Access-Control-Allow-Origin") not in (
            "https://evil.example.com",
            "*",
        )
