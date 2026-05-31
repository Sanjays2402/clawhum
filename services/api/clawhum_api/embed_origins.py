"""Per-workspace embed origin allowlist.

Why this exists
---------------
Workspaces produce public share links (``/r/{id}``) that can be framed
on third party sites via the oEmbed endpoint and the embed page. Many
enterprise buyers will not approve a vendor that lets *any* origin
frame their workspace content, because that is an XSS/clickjacking
amplification path against their own users.

This module stores a per-tenant list of approved origins (scheme +
host + optional port, no path, no query). When a workspace has at
least one origin registered the embed surface is enforced:

* the oEmbed JSON endpoint rejects requests whose ``Origin`` request
  header is not in the share's tenant allowlist with a 403,
* the public embed HTML page is served with a tight
  ``Content-Security-Policy: frame-ancestors`` header derived from
  the same list,
* ``GET /share/{id}`` includes ``embed_allowed_origins`` so SDKs and
  the dashboard can mirror the policy client-side.

An empty list is "no restriction" so existing tenants keep working
unchanged. Cross-tenant lookups are impossible because the store is
keyed by tenant id, mirroring the same JSONL pattern used by
``ip_allowlist`` and ``sessions``.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from clawhum_core.settings import get_settings

_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_CACHE: dict[str, list["Origin"]] | None = None
_CACHE_PATH: Path | None = None

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$")


@dataclass(frozen=True)
class Origin:
    id: str
    tenant_id: str
    origin: str  # canonical form: scheme://host[:port], lowercase
    label: str
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "origin": self.origin,
            "label": self.label,
            "created_at": self.created_at,
        }


def _new_id() -> str:
    return "eor_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def normalize_origin(raw: str) -> str:
    """Return canonical scheme://host[:port], or raise ValueError.

    Strips any path/query/fragment. Lowercases scheme + host. Drops
    the default port for the scheme so ``https://x.com:443`` and
    ``https://x.com`` compare equal.
    """
    if not raw or len(raw) > 256:
        raise ValueError("origin must be 1..256 chars")
    s = raw.strip()
    if "://" not in s:
        raise ValueError("origin must include scheme, e.g. https://app.acme.com")
    parts = urlsplit(s)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("only http and https origins are supported")
    host = (parts.hostname or "").lower()
    if not host or not _HOST_RE.match(host):
        raise ValueError("invalid host")
    port = parts.port
    if port is not None and (port < 1 or port > 65535):
        raise ValueError("invalid port")
    default_port = 443 if scheme == "https" else 80
    if port is None or port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _path() -> Path:
    return Path(get_settings().embed_origins_path)


def _load_locked() -> dict[str, list[Origin]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, list[Origin]] = {}
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
                if row.get("_deleted"):
                    rid = row.get("id")
                    if rid:
                        for bucket in out.values():
                            bucket[:] = [o for o in bucket if o.id != rid]
                    continue
                try:
                    origin = Origin(
                        id=str(row["id"]),
                        tenant_id=str(row.get("tenant_id") or "default"),
                        origin=str(row["origin"]),
                        label=str(row.get("label") or ""),
                        created_at=float(row.get("created_at") or 0.0),
                    )
                except (KeyError, ValueError, TypeError):
                    continue
                out.setdefault(origin.tenant_id, []).append(origin)
    _CACHE = out
    _CACHE_PATH = p
    return out


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def list_origins(tenant_id: str) -> list[Origin]:
    with _LOCK:
        store = _load_locked()
        return list(store.get(tenant_id, []))


def add_origin(tenant_id: str, raw: str, label: str = "") -> Origin:
    canonical = normalize_origin(raw)  # raises ValueError on bad input
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        for existing in bucket:
            if existing.origin == canonical:
                # idempotent: re-adding the same origin returns the existing row
                return existing
        row = Origin(
            id=_new_id(),
            tenant_id=tenant_id,
            origin=canonical,
            label=label.strip()[:120],
            created_at=time.time(),
        )
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row.to_dict()) + "\n")
        store.setdefault(tenant_id, []).append(row)
        return row


def delete_origin(tenant_id: str, origin_id: str) -> bool:
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        target = next((o for o in bucket if o.id == origin_id), None)
        if target is None:
            return False
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": origin_id, "tenant_id": tenant_id, "_deleted": True}) + "\n")
        store[tenant_id] = [o for o in bucket if o.id != origin_id]
    return True


def is_allowed(tenant_id: str, request_origin: str | None) -> bool:
    """True when the origin is approved, or when the tenant has no rules.

    Empty rule set = no restriction (opt-in feature). A missing or
    malformed ``Origin`` header on a tenant with rules is denied
    fail-closed.
    """
    rules = list_origins(tenant_id)
    if not rules:
        return True
    if not request_origin:
        return False
    try:
        canonical = normalize_origin(request_origin)
    except ValueError:
        return False
    return any(r.origin == canonical for r in rules)


def has_rules(tenant_id: str) -> bool:
    return bool(list_origins(tenant_id))


def frame_ancestors_csp(tenant_id: str) -> str:
    """Build a ``frame-ancestors`` directive value for the tenant.

    Returns ``'none'`` if rules exist but are empty after filtering
    (defensive; the caller usually checks ``has_rules`` first).
    Returns the space-joined origin list when rules exist. The caller
    decides what to do when no rules exist (typically allow all by
    omitting the header).
    """
    rules = list_origins(tenant_id)
    if not rules:
        return "*"
    return " ".join(r.origin for r in rules) or "'none'"
