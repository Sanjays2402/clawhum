"""SCIM bearer token store.

Enterprise identity providers (Okta, Azure AD, Google Workspace) push
user lifecycle events into SCIM 2.0 endpoints authenticated with a
static bearer token. We keep one token per workspace; the plaintext is
shown to the admin exactly once at mint time, only a SHA-256 hash is
persisted. Rotation appends a new row and tombstones the old hash so
the on disk JSONL log stays append only and replayable, matching the
pattern used by api_keys, member_store, and webhook secrets.

This module is storage only: no FastAPI, no HTTP. The routes layer
enforces admin + MFA at mint time and validates the bearer at every
SCIM call. Audit log writes happen via the global AuditLogMiddleware.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings

_LOCK = Lock()

SCIM_TOKEN_PREFIX = "scim_"


def new_token() -> str:
    """Mint a fresh SCIM bearer token. Shown to the admin exactly once."""
    return SCIM_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScimToken:
    tenant_id: str
    token_hash: str
    created_by: str
    created_at: float
    last_used_at: float = 0.0
    revoked: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
        }


def _path() -> Path:
    p = Path(get_settings().scim_tokens_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.touch()
    return p


def _append(record: dict[str, Any]) -> None:
    path = _path()
    line = json.dumps(record, separators=(",", ":"), sort_keys=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _load_all() -> list[ScimToken]:
    """Return every row in file order. Caller filters by tenant + revoked."""
    out: list[ScimToken] = []
    with _path().open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                out.append(
                    ScimToken(
                        tenant_id=rec.get("tenant_id", ""),
                        token_hash=rec.get("token_hash", ""),
                        created_by=rec.get("created_by", ""),
                        created_at=float(rec.get("created_at", 0.0)),
                        last_used_at=float(rec.get("last_used_at", 0.0)),
                        revoked=bool(rec.get("revoked", False)),
                    )
                )
            except (TypeError, ValueError):
                continue
    return out


def get_active(tenant_id: str) -> ScimToken | None:
    """Return the current active token row for a tenant, or None."""
    tenant_id = (tenant_id or "").strip().lower()
    latest: ScimToken | None = None
    for row in _load_all():
        if row.tenant_id != tenant_id:
            continue
        if row.revoked:
            latest = None
            continue
        latest = row
    return latest


def mint(*, tenant_id: str, created_by: str, now: float | None = None) -> tuple[ScimToken, str]:
    """Create a new SCIM token for a tenant. Revokes any previous one.

    Returns the persisted row plus the plaintext token; the caller
    must show the plaintext exactly once and then discard it.
    """
    tenant_id = (tenant_id or "").strip().lower()
    if not tenant_id:
        raise ValueError("tenant_id is required")
    now = time.time() if now is None else now
    # Tombstone any active token so each tenant has at most one live token.
    existing = get_active(tenant_id)
    if existing is not None:
        _append({**asdict(existing), "revoked": True})
    token = new_token()
    row = ScimToken(
        tenant_id=tenant_id,
        token_hash=hash_token(token),
        created_by=created_by or "unknown",
        created_at=now,
        last_used_at=0.0,
        revoked=False,
    )
    _append(asdict(row))
    return row, token


def revoke(tenant_id: str) -> bool:
    """Tombstone the active token for a tenant. Returns True if one existed."""
    existing = get_active(tenant_id)
    if existing is None:
        return False
    _append({**asdict(existing), "revoked": True})
    return True


def lookup(token: str) -> ScimToken | None:
    """Constant-time match a presented bearer token to its tenant row.

    Returns None for unknown, revoked, or malformed tokens so callers
    can return a uniform 401 without leaking which case applied.
    """
    if not token or not token.startswith(SCIM_TOKEN_PREFIX):
        return None
    target = hash_token(token)
    for row in _load_all():
        if row.revoked:
            continue
        if secrets.compare_digest(row.token_hash, target):
            return row
    return None


def touch_last_used(tenant_id: str, *, now: float | None = None) -> None:
    """Best-effort last-used timestamp. Failures must not block SCIM calls."""
    row = get_active(tenant_id)
    if row is None:
        return
    now = time.time() if now is None else now
    _append({**asdict(row), "last_used_at": now})


def reset_for_tests() -> None:
    """Truncate the on disk log. Tests only."""
    path = _path()
    with _LOCK:
        path.write_text("", encoding="utf-8")
