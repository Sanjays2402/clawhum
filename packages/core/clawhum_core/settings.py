from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAWHUM_", env_file=".env", extra="ignore")

    api_key: str = Field(default="changeme", description="Legacy single API key. Prefer api_keys.")
    api_keys: str = Field(
        default="",
        description=(
            "Multi-key spec: 'name:secret:rpm,name2:secret2:rpm'. "
            "rpm is optional and defaults to the rate-limit default."
        ),
    )
    rate_limit_per_minute: int = Field(
        default=120, description="Default requests-per-minute applied per API key or per IP."
    )
    log_level: str = "INFO"
    log_json: bool = True

    index_path: Path = Path("./data/index/clawhum.faiss")
    metadata_path: Path = Path("./data/index/metadata.jsonl")
    library_path: Path = Path("./data/audio")
    feedback_path: Path = Path("./data/feedback.jsonl")
    shares_path: Path = Path("./data/shares.jsonl")
    collections_path: Path = Path("./data/collections.jsonl")
    history_path: Path = Path("./data/history.jsonl")
    history_views_path: Path = Path("./data/history_views.jsonl")
    usage_path: Path = Path("./data/usage.jsonl")
    webhooks_path: Path = Path("./data/webhooks.jsonl")
    webhook_deliveries_path: Path = Path("./data/webhook_deliveries.jsonl")
    webhook_timeout_sec: float = 5.0
    webhook_max_attempts: int = 3
    audit_log_path: Path = Path("./data/audit.jsonl")
    audit_enabled: bool = True
    # Size-based rotation for the audit JSONL file. When the active file
    # exceeds audit_max_bytes, it is renamed with a numeric suffix and a
    # fresh file is started. audit_backup_count is the maximum number of
    # rotated files kept on disk; older files are deleted. Set
    # audit_max_bytes to 0 to disable in process rotation entirely and
    # fall back to external rotation (logrotate, sidecar).
    audit_max_bytes: int = Field(
        default=50 * 1024 * 1024,
        description="Rotate the audit log when it exceeds this many bytes. 0 disables rotation.",
    )
    audit_backup_count: int = Field(
        default=5,
        description="Maximum number of rotated audit log files to retain.",
    )

    model_id: str = "laion/clap-htsat-unfused"
    device: str = "auto"
    embed_dim: int = 512
    target_sr: int = 48000
    segment_seconds: float = 6.0
    segment_hop_seconds: float = 3.0

    top_k: int = 10
    threshold: float = 0.20

    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:7451/auth/spotify/callback"

    otel_endpoint: str = ""
    service_name: str = "clawhum-api"

    # CORS and HTTP security headers. cors_allow_origins is a comma
    # separated list of exact origins (no wildcards in production); the
    # legacy default of "*" stays for local dev but operators are
    # expected to pin it. cors_allow_credentials only takes effect when
    # origins are not the wildcard. Security headers are emitted by
    # SecurityHeadersMiddleware on every response; HSTS is only sent
    # when the request was served over HTTPS (or behind a TLS
    # terminating proxy that sets X-Forwarded-Proto: https) so local
    # http://127.0.0.1 development does not get pinned.
    cors_allow_origins: str = Field(
        default="*",
        description=(
            "Comma separated list of allowed CORS origins, or '*' for any. "
            "Set to an explicit list in production."
        ),
    )
    cors_allow_credentials: bool = False
    cors_allow_methods: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    cors_allow_headers: str = "Authorization,Content-Type,X-API-Key,X-Request-ID,traceparent"
    security_headers_enabled: bool = True
    security_hsts_max_age: int = Field(
        default=63072000,
        description="Strict-Transport-Security max-age in seconds. 2 years by default.",
    )
    security_hsts_include_subdomains: bool = True
    security_hsts_preload: bool = False
    security_csp: str = Field(
        default="default-src 'none'; frame-ancestors 'none'",
        description=(
            "Content-Security-Policy header value for API responses. The default "
            "locks everything down since the API serves JSON, not HTML. Set to an "
            "empty string to disable the CSP header entirely."
        ),
    )
    security_referrer_policy: str = "no-referrer"
    security_permissions_policy: str = "geolocation=(), microphone=(), camera=()"

    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    def cors_methods_list(self) -> list[str]:
        raw = (self.cors_allow_methods or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [m.strip().upper() for m in raw.split(",") if m.strip()]

    def cors_headers_list(self) -> list[str]:
        raw = (self.cors_allow_headers or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [h.strip() for h in raw.split(",") if h.strip()]

    # Sentry error tracking. Empty DSN disables the integration entirely.
    sentry_dsn: str = ""
    sentry_environment: str = ""
    sentry_traces_sample_rate: float = 0.0
    sentry_profiles_sample_rate: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
