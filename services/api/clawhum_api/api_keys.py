"""API key registry with optional per-key rate limits.

Parses a compact spec from the CLAWHUM_API_KEYS environment variable:

    CLAWHUM_API_KEYS="ops:sk_live_abc:600,partner:sk_live_xyz:120,readonly:sk_ro_qqq"

Each entry is name:key[:requests_per_minute]. The legacy single key
CLAWHUM_API_KEY is still honoured and registered as the "default" key
when no multi-key spec is provided, preserving backwards compatibility.

The registry is intentionally tiny and dependency free so it can be
imported from auth and middleware without coupling them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from clawhum_core.settings import get_settings


@dataclass(frozen=True)
class APIKey:
    name: str
    secret: str
    rpm: int  # 0 means "use middleware default"


@dataclass(frozen=True)
class KeyRegistry:
    by_secret: dict[str, APIKey]
    default_rpm: int

    def lookup(self, secret: str | None) -> APIKey | None:
        if not secret:
            return None
        return self.by_secret.get(secret)

    def is_open(self) -> bool:
        """True when no real auth is configured (dev mode)."""
        return not self.by_secret


def _parse_spec(spec: str, default_rpm: int) -> dict[str, APIKey]:
    out: dict[str, APIKey] = {}
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 2:
            # Malformed entry, skip rather than crash boot. Operator
            # will see the missing key when auth fails.
            continue
        name = parts[0].strip()
        secret = parts[1].strip()
        rpm = default_rpm
        if len(parts) >= 3 and parts[2].strip():
            try:
                rpm = max(1, int(parts[2].strip()))
            except ValueError:
                rpm = default_rpm
        if not name or not secret:
            continue
        out[secret] = APIKey(name=name, secret=secret, rpm=rpm)
    return out


def build_registry(default_rpm: int = 120) -> KeyRegistry:
    s = get_settings()
    spec = (s.api_keys or "").strip()
    if spec:
        return KeyRegistry(by_secret=_parse_spec(spec, default_rpm), default_rpm=default_rpm)
    # Legacy single-key path.
    if s.api_key and s.api_key != "changeme":
        legacy = APIKey(name="default", secret=s.api_key, rpm=default_rpm)
        return KeyRegistry(by_secret={legacy.secret: legacy}, default_rpm=default_rpm)
    # Dev mode: no auth configured.
    return KeyRegistry(by_secret={}, default_rpm=default_rpm)


@lru_cache
def get_registry() -> KeyRegistry:
    return build_registry()


def reset_registry_cache() -> None:
    """Test hook: drop the cached registry so settings changes take effect."""
    get_registry.cache_clear()
