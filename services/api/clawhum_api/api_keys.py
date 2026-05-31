"""API key registry with optional per-key rate limits and roles.

Parses a compact spec from the CLAWHUM_API_KEYS environment variable:

    CLAWHUM_API_KEYS="ops:sk_live_abc:600:admin|writer:acme,partner:sk_live_xyz:120:writer:globex,readonly:sk_ro_qqq::reader"

Each entry is name:key[:requests_per_minute[:role1|role2|...[:tenant_id]]].
When tenant_id is omitted it defaults to the key name, so single tenant
deployments keep working without any configuration change.
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

# Canonical fine-grained scope set for personal access tokens.
#
# Roles are coarse (admin / writer / reader) and govern what a human
# operator can do via the dashboard. Scopes are the contract a machine
# token signs: a CI job that only needs to call /match should be able
# to mint a token with read:matches and nothing else, so a leak of
# that token cannot rewrite the library or rotate keys. Enterprise
# procurement reviews specifically ask for this least-privilege shape.
#
# A PAT may carry zero scopes; in that case it is treated as "all
# scopes the role set permits" so existing tokens minted before this
# release keep working unchanged. The `admin` role always implies
# every scope, mirroring role semantics.
SCOPES: frozenset[str] = frozenset({
    "read:matches",
    "write:matches",
    "read:library",
    "write:library",
    "read:keys",
    "write:keys",
    "admin",
})

# Map each scope to the minimum role that may mint or use it. This
# keeps a `reader` PAT from being upgraded with a `write:*` scope
# at mint time, regardless of what the body asked for.
SCOPE_MIN_ROLE: dict[str, str] = {
    "read:matches": "reader",
    "write:matches": "writer",
    "read:library": "reader",
    "write:library": "writer",
    "read:keys": "reader",
    "write:keys": "writer",
    "admin": "admin",
}


def normalise_scopes(values) -> frozenset[str]:
    """Lower-case, dedupe, drop anything outside the canonical set.

    Silently dropping unknown scopes (rather than raising) matches how
    roles are parsed and prevents a typo in a customer integration
    from accidentally granting wider access than intended.
    """
    if not values:
        return frozenset()
    parts = {str(v).strip().lower() for v in values if v is not None}
    return frozenset(parts & SCOPES)


def scopes_allowed_for_roles(roles: frozenset[str]) -> frozenset[str]:
    """Return the maximum scope set a caller with these roles may hold."""
    if ADMIN_ROLE in roles:
        return SCOPES
    out: set[str] = set()
    for scope, min_role in SCOPE_MIN_ROLE.items():
        if min_role == ADMIN_ROLE:
            continue
        if min_role == "writer" and "writer" in roles:
            out.add(scope)
        elif min_role == "reader" and ("reader" in roles or "writer" in roles):
            out.add(scope)
    return frozenset(out)


# Sentinel tenant used when auth is open (dev mode) or no key matched.
DEV_TENANT_ID = "dev"
ANON_TENANT_ID = "anonymous"


@dataclass(frozen=True)
class APIKey:
    name: str
    secret: str
    rpm: int  # 0 means "use middleware default"
    roles: frozenset[str] = field(default_factory=frozenset)
    tenant_id: str = ""

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
        tenant_id = name
        if len(parts) >= 5 and parts[4].strip():
            tenant_id = _normalise_tenant_id(parts[4])
        if not name or not secret:
            continue
        out[secret] = APIKey(
            name=name, secret=secret, rpm=rpm, roles=roles, tenant_id=tenant_id,
        )
    return out


_TENANT_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _normalise_tenant_id(raw: str) -> str:
    """Lowercase, strip, and keep only safe id chars. Empty -> "default".

    Tenant ids land in filenames and log fields, so we lock the alphabet
    down to ascii lowercase plus -_ to avoid path traversal or log
    injection surprises.
    """
    s = raw.strip().lower()
    cleaned = "".join(c for c in s if c in _TENANT_ALLOWED)
    return cleaned or "default"


def build_registry(default_rpm: int = 120) -> KeyRegistry:
    s = get_settings()
    spec = (s.api_keys or "").strip()
    if spec:
        return KeyRegistry(by_secret=_parse_spec(spec, default_rpm), default_rpm=default_rpm)
    # Legacy single-key path. Legacy keys keep full access.
    if s.api_key and s.api_key != "changeme":
        legacy = APIKey(
            name="default",
            secret=s.api_key,
            rpm=default_rpm,
            roles=ROLES,
            tenant_id="default",
        )
        return KeyRegistry(by_secret={legacy.secret: legacy}, default_rpm=default_rpm)
    # Dev mode: no auth configured.
    return KeyRegistry(by_secret={}, default_rpm=default_rpm)


@lru_cache
def get_registry() -> KeyRegistry:
    return build_registry()


def reset_registry_cache() -> None:
    """Test hook: drop the cached registry so settings changes take effect."""
    get_registry.cache_clear()
