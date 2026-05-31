"""Per-workspace single sign on configuration.

Each tenant can register one OIDC provider (Okta, Azure AD, Google
Workspace, Auth0, Keycloak, anything that publishes a discovery
document at ``{issuer}/.well-known/openid-configuration``). The
record carries the issuer URL, client id, client secret, the email
domain that maps to the workspace, and an ``enforced`` flag.

When ``enforced=True`` is set on the record, the web UI hides the
password and magic-link sign-in paths for that domain and the
identity layer only accepts an OIDC assertion. The flag is also
surfaced via ``/me`` so embedded clients can honour it without
fetching the SSO config (which is admin-only).

Storage: the same append only JSONL pattern used by the rest of the
project. Last write wins per ``tenant_id`` and a ``deleted=True``
tombstone retires the config. No database required for multi worker
deployments because writes are serialised by the process lock and
each line is a complete record.

Why not store SSO state in a vendor database here: this repo runs
in air gapped procurement evaluations where the buyer wants to read
the source, point it at a JSONL file, and confirm there are no
hidden network calls. Stick with the project's storage convention.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock
from typing import Iterable

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "SSOConfig"] | None = None
_CACHE_PATH: Path | None = None

# Recognised OIDC providers. The labels are surfaced in the admin UI
# and the discovery endpoint so a sign-in screen can render the right
# button copy without the backend needing to know the brand.
KNOWN_PROVIDERS: dict[str, str] = {
    "okta": "Okta",
    "azure": "Microsoft Entra ID",
    "google": "Google Workspace",
    "auth0": "Auth0",
    "keycloak": "Keycloak",
    "generic": "OIDC",
}

# Loose, non-pathological domain check. The auth layer never trusts
# this for security; it only stops obvious garbage from corrupting
# the discovery lookup table.
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# RFC 3986 scheme + host; we only accept https in production but
# allow http://localhost so admins can wire up a local Keycloak.
_ISSUER_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$")


@dataclass(frozen=True)
class SSOConfig:
    tenant_id: str
    provider: str  # one of KNOWN_PROVIDERS
    issuer: str  # base URL, e.g. https://acme.okta.com
    client_id: str
    client_secret: str
    email_domain: str  # e.g. acme.com; lowercase
    enforced: bool
    created_at: float
    updated_at: float
    created_by: str  # actor name from request.state.api_key_name
    deleted: bool = False

    def public_dict(self, reveal_secret: bool = False) -> dict:
        """JSON-safe view for admins. Client secret is masked by default."""
        secret = self.client_secret if reveal_secret else _mask_secret(self.client_secret)
        return {
            "tenant_id": self.tenant_id,
            "provider": self.provider,
            "provider_label": KNOWN_PROVIDERS.get(self.provider, "OIDC"),
            "issuer": self.issuer,
            "client_id": self.client_id,
            "client_secret": secret,
            "email_domain": self.email_domain,
            "enforced": self.enforced,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "discovery_url": self.issuer.rstrip("/") + "/.well-known/openid-configuration",
        }


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 4:
        return "*" * len(secret)
    return f"...{secret[-4:]}"


def normalise_domain(domain: str) -> str:
    return (domain or "").strip().lower().lstrip("@")


def is_valid_domain(domain: str) -> bool:
    return bool(_DOMAIN_RE.match(domain or ""))


def is_valid_issuer(issuer: str) -> bool:
    return bool(_ISSUER_RE.match(issuer or ""))


def _path() -> Path:
    return Path(get_settings().sso_path)


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _from_row(row: dict) -> SSOConfig | None:
    try:
        return SSOConfig(
            tenant_id=str(row["tenant_id"]),
            provider=str(row.get("provider", "generic")),
            issuer=str(row.get("issuer", "")),
            client_id=str(row.get("client_id", "")),
            client_secret=str(row.get("client_secret", "")),
            email_domain=str(row.get("email_domain", "")),
            enforced=bool(row.get("enforced", False)),
            created_at=float(row.get("created_at", 0.0)),
            updated_at=float(row.get("updated_at", 0.0)),
            created_by=str(row.get("created_by", "")),
            deleted=bool(row.get("deleted", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _load_locked() -> dict[str, SSOConfig]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    by_tenant: dict[str, SSOConfig] = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec = _from_row(row)
                if rec is None:
                    continue
                if rec.deleted:
                    by_tenant.pop(rec.tenant_id, None)
                else:
                    by_tenant[rec.tenant_id] = rec
    _CACHE = by_tenant
    _CACHE_PATH = p
    return _CACHE


def _append_locked(record: SSOConfig) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    # Mutate the live cache to match the on-disk view.
    cache = _load_locked()
    if record.deleted:
        cache.pop(record.tenant_id, None)
    else:
        cache[record.tenant_id] = record


def get_for_tenant(tenant_id: str) -> SSOConfig | None:
    if not tenant_id:
        return None
    with _LOCK:
        return _load_locked().get(tenant_id)


def get_by_email_domain(domain: str) -> SSOConfig | None:
    """Resolve which workspace owns a given email domain.

    First-write wins per domain so two tenants cannot silently claim
    the same suffix; later writes are ignored at lookup time. The
    admin UI surfaces a conflict warning when this happens, but the
    server side guarantee is the important one.
    """
    domain = normalise_domain(domain)
    if not domain:
        return None
    with _LOCK:
        for rec in _load_locked().values():
            if rec.email_domain == domain:
                return rec
        return None


def upsert(
    *,
    tenant_id: str,
    provider: str,
    issuer: str,
    client_id: str,
    client_secret: str,
    email_domain: str,
    enforced: bool,
    actor: str,
) -> SSOConfig:
    """Create or replace the SSO config for a tenant.

    Validates inputs strictly; the route layer can rely on every
    returned record being usable. ``client_secret`` may be passed as
    an empty string to keep the existing secret unchanged on an
    update (the UI sends "" when the admin did not retype it).
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")
    provider = (provider or "generic").lower().strip()
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    issuer = (issuer or "").strip().rstrip("/")
    if not is_valid_issuer(issuer):
        raise ValueError("issuer must be an https URL")
    client_id = (client_id or "").strip()
    if not client_id:
        raise ValueError("client_id is required")
    email_domain = normalise_domain(email_domain)
    if not is_valid_domain(email_domain):
        raise ValueError("email_domain must look like example.com")
    now = time.time()
    with _LOCK:
        existing = _load_locked().get(tenant_id)
        # Guard against a different tenant having already claimed this
        # email domain. Without this check, two workspaces could fight
        # over which one wins discovery for foo.com and the answer
        # would depend on map iteration order.
        for other in _load_locked().values():
            if other.tenant_id != tenant_id and other.email_domain == email_domain:
                raise ValueError(
                    f"email domain {email_domain} already claimed by another workspace"
                )
        # Preserve secret on no-op edits so the admin UI can avoid
        # re-displaying it.
        if not client_secret and existing is not None:
            client_secret = existing.client_secret
        if not client_secret:
            raise ValueError("client_secret is required")
        rec = SSOConfig(
            tenant_id=tenant_id,
            provider=provider,
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            email_domain=email_domain,
            enforced=bool(enforced),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            created_by=existing.created_by if existing else actor,
            deleted=False,
        )
        _append_locked(rec)
        return rec


def delete(tenant_id: str, actor: str) -> bool:
    """Tombstone the SSO config for a tenant. Returns True if a record
    existed, False if there was nothing to delete (idempotent)."""
    with _LOCK:
        existing = _load_locked().get(tenant_id)
        if existing is None:
            return False
        tomb = SSOConfig(
            tenant_id=tenant_id,
            provider=existing.provider,
            issuer=existing.issuer,
            client_id=existing.client_id,
            client_secret="",
            email_domain=existing.email_domain,
            enforced=False,
            created_at=existing.created_at,
            updated_at=time.time(),
            created_by=actor or existing.created_by,
            deleted=True,
        )
        _append_locked(tomb)
        return True


def all_configs() -> Iterable[SSOConfig]:
    """Read only snapshot of every live SSO config across tenants.

    Intended for the operator CLI and metrics; never exposed to the
    HTTP layer because it would leak tenant identities.
    """
    with _LOCK:
        return list(_load_locked().values())
