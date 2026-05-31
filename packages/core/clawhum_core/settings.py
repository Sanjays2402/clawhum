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
    pat_path: Path = Path("./data/personal_access_tokens.jsonl")
    mfa_path: Path = Path("./data/mfa.jsonl")
    quota_path: Path = Path("./data/quotas.jsonl")
    residency_path: Path = Path("./data/residency.jsonl")
    region: str = Field(
        default="unset",
        description=(
            "Region this API node is deployed in. One of: us, eu, apac, "
            "unset. When set to a real region, mutating requests are "
            "rejected with 451 if the workspace is pinned to a different "
            "region and enforcement is on. 'unset' disables the check "
            "globally so single region installs are unaffected."
        ),
    )
    residency_enforcement: bool = Field(
        default=True,
        description=(
            "Master switch for the residency middleware. When false the "
            "node still advertises X-Data-Region on responses but never "
            "blocks. Per tenant 'enforce' must also be true for a 451 "
            "to fire, so adoption stays opt in per workspace."
        ),
    )
    mfa_required_for_admin: bool = Field(
        default=True,
        description=(
            "When true, an admin actor that has verified MFA must present "
            "a fresh code in X-MFA-Code for destructive endpoints (key "
            "revoke, data delete, IP allowlist mutate, webhook delete). "
            "Actors who have never enrolled are not blocked, so adoption "
            "is opt-in per actor while enforcement is global."
        ),
    )
    pat_max_ttl_days: int = Field(
        default=365,
        description=(
            "Hard upper bound on PAT lifetime, in days. Mint requests "
            "that ask for a longer TTL are clamped. 0 disables the cap "
            "(tokens can live forever, not recommended for production)."
        ),
    )
    pat_default_ttl_days: int = Field(
        default=90,
        description=(
            "Default PAT lifetime in days when the caller does not pick "
            "one. 0 means tokens default to non-expiring. Always bounded "
            "by pat_max_ttl_days when that cap is enabled."
        ),
    )
    pat_rotation_max_grace_minutes: int = Field(
        default=60,
        description=(
            "Maximum grace window (minutes) during which a rotated PAT's "
            "previous secret keeps authenticating. Mint and rotate calls "
            "that ask for a longer grace are clamped down. 0 disables "
            "grace entirely so rotation revokes the old secret immediately."
        ),
    )
    members_path: Path = Path("./data/members.jsonl")
    seat_limits_path: Path = Path("./data/seat_limits.jsonl")
    member_invite_ttl_hours: int = Field(
        default=168,
        description=(
            "Lifetime of a pending member invite token in hours. After "
            "this window the invite cannot be accepted and must be "
            "re-issued by a workspace admin. 0 disables expiry (not "
            "recommended; auditors flag dangling invites)."
        ),
    )
    scim_tokens_path: Path = Path("./data/scim_tokens.jsonl")
    scim_enabled: bool = Field(
        default=True,
        description=(
            "When true, the SCIM 2.0 /scim/v2 endpoints are mounted and "
            "identity providers (Okta, Azure AD, Google Workspace) can "
            "provision and de-provision workspace members using a "
            "per-tenant bearer token. Mutations still write to the same "
            "member_store and audit log as the human admin console so a "
            "single source of truth is preserved."
        ),
    )
    ip_allowlist_path: Path = Path("./data/ip_allowlist.jsonl")
    sessions_path: Path = Path("./data/sessions.jsonl")
    session_policy_path: Path = Path("./data/session_policy.jsonl")
    ip_allowlist_enabled: bool = Field(
        default=True,
        description=(
            "When true, tenants with at least one IP allowlist rule must "
            "originate requests from a matching CIDR. Empty rule sets are "
            "always allowed so the feature is opt-in per tenant."
        ),
    )
    sso_path: Path = Path("./data/sso.jsonl")
    sso_default_redirect_uri: str = Field(
        default="http://127.0.0.1:7451/auth/sso/callback",
        description=(
            "Default OIDC redirect URI shown in the SSO admin UI when a "
            "workspace owner is wiring up their identity provider. The "
            "backend never performs the redirect itself; this string is "
            "surfaced read only so admins can paste it into Okta, Azure "
            "AD, or Google Workspace without guessing."
        ),
    )
    webhook_deliveries_path: Path = Path("./data/webhook_deliveries.jsonl")
    webhook_timeout_sec: float = 5.0
    webhook_max_attempts: int = 3
    webhook_allowlist_path: Path = Path("./data/webhook_allowlist.jsonl")
    webhook_block_private_ips: bool = Field(
        default=True,
        description=(
            "When true, outbound webhook destinations that resolve to"
            " loopback, link local, multicast, or RFC1918 ranges are"
            " rejected at registration and re-checked before each"
            " delivery to defeat DNS rebinding. Workspace owners can"
            " allowlist specific host suffixes to relax this for on prem"
            " receivers; cloud metadata endpoints stay denied either way."
        ),
    )
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

    # Idempotency-Key support for mutating routes. Enterprise integrators
    # retry POST/PUT/PATCH/DELETE on timeouts, and without server-side
    # de-duplication a retry can double-write a row. The middleware
    # caches the first response keyed by (tenant, key, body-hash) for
    # ``idempotency_ttl_seconds`` and replays it on subsequent calls.
    idempotency_enabled: bool = Field(
        default=True,
        description="Enable Idempotency-Key replay cache for mutating HTTP methods.",
    )
    idempotency_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        description="How long a cached idempotent response stays replayable.",
    )
    idempotency_max_per_tenant: int = Field(
        default=1024,
        description="Hard cap on cached idempotent responses per tenant (LRU evicted).",
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
