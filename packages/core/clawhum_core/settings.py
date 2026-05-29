from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAWHUM_", env_file=".env", extra="ignore")

    api_key: str = Field(default="changeme", description="Static API key for /match etc.")
    log_level: str = "INFO"
    log_json: bool = True

    index_path: Path = Path("./data/index/clawhum.faiss")
    metadata_path: Path = Path("./data/index/metadata.jsonl")
    library_path: Path = Path("./data/audio")
    feedback_path: Path = Path("./data/feedback.jsonl")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
