"""API key registry with optional per-key rate limits and roles.

Parses a compact spec from the CLAWHUM_API_KEYS environment variable:

    CLAWHUM_API_KEYS="ops:sk_live_abc:600:admin|writer,partner:sk_live_xyz:120:writer,readonly:sk_ro_qqq::reader"

Each entry is name:key[:requests_per_minute[:role1|role2|...]].
The legacy single key CLAWHUM_API_KEY is still honoured and registered
as the "default" key when no multi-key spec is provided. Legacy keys
receive the full role set so they keep working unchanged.

Known roles (see ROLES below): admin, writer, reader. Routes declare
required roles via require_roles(...) in auth.py; the dependency holds
the wire format stable while letting operators tighten access without
code changes.

The registry is intentionally tiny and dependency free so it can be
imported from auth and middleware without coupling them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from clawhum_core.settings import get_settings

# Canonical role set. "admin" implies every other role.
ROLES: frozenset[str] = frozenset({"admin", "writer", "reader"})
ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class APIKey:
    name: str
    secret: str
    rpm: int  # 0 means "use middleware default"
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_role(self, role: str) -> bool:
        return ADMIN_ROLE in self.roles or role in self.roles

    def has_any(self, required: frozenset[str]) -> bool:
        if not required:
            return True
        if ADMIN_ROLE in self.roles:
            return True
        return bool(self.roles & required)


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


def _parse_roles(raw: str) -> frozenset[str]:
    parts = {p.strip().lower() for p in raw.split("|") if p.strip()}
    # Silently drop unknown roles so a typo cannot grant unintended access.
    return frozenset(parts & ROLES)


def _parse_spec(spec: str, default_rpm: int) -> dict[str, APIKey]:
    out: dict[str, APIKey] = {}
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 2:
            # Malformed entry, skip rather than crash boot.
            continue
        name = parts[0].strip()
        secret = parts[1].strip()
        rpm = default_rpm
        if len(parts) >= 3 and parts[2].strip():
            try:
                rpm = max(1, int(parts[2].strip()))
            except ValueError:
                rpm = default_rpm
        roles: frozenset[str] = ROLES  # default: full access, matches legacy
        if len(parts) >= 4 and parts[3].strip():
            roles = _parse_roles(parts[3])
        if not name or not secret:
            continue
        out[secret] = APIKey(name=name, secret=secret, rpm=rpm, roles=roles)
    return out


def build_registry(default_rpm: int = 120) -> KeyRegistry:
    s = get_settings()
    spec = (s.api_keys or "").strip()
    if spec:
        return KeyRegistry(by_secret=_parse_spec(spec, default_rpm), default_rpm=default_rpm)
    # Legacy single-key path. Legacy keys keep full access.
    if s.api_key and s.api_key != "changeme":
        legacy = APIKey(name="default", secret=s.api_key, rpm=default_rpm, roles=ROLES)
        return KeyRegistry(by_secret={legacy.secret: legacy}, default_rpm=default_rpm)
    # Dev mode: no auth configured.
    return KeyRegistry(by_secret={}, default_rpm=default_rpm)


@lru_cache
def get_registry() -> KeyRegistry:
    return build_registry()


def reset_registry_cache() -> None:
    """Test hook: drop the cached registry so settings changes take effect."""
    get_registry.cache_clear()
