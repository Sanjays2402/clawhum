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

    # Sentry error tracking. Empty DSN disables the integration entirely.
    sentry_dsn: str = ""
    sentry_environment: str = ""
    sentry_traces_sample_rate: float = 0.0
    sentry_profiles_sample_rate: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
