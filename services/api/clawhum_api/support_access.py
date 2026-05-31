"""Per-workspace Support Access Grants.

Why this exists
---------------
Every enterprise procurement review asks the same question: "When your
support staff need to look at our data to debug an incident, how is
that access authorised, scoped, time-boxed, and recorded?" Buyers
will not sign a contract that lets the vendor's employees freely
read or modify their tenant data on demand. SOC2 CC6.1, ISO 27001
A.9.2.3, and HIPAA 164.308(a)(4) all require named, approved,
time-bounded access for privileged third parties.

This module owns the customer-side approval record for that access.
Each workspace can:

* See whether support access is currently allowed at all (a kill
  switch defaulting to off when no grants exist).
* Grant a named clawhum support actor (identified by email) either
  ``read`` or ``write`` access for a bounded window, with a stated
  reason that lands in the audit log.
* See every active grant, its scope, expiry, who approved it, and
  the reason.
* Revoke a grant instantly. Revocation takes effect on the next
  authenticated request that carries the ``X-Support-Actor`` header.

When a request arrives with ``X-Support-Actor: <email>``, the auth
layer looks up an active grant for that email in the caller's
workspace. With no active grant, the request is rejected 403; the
support staffer cannot proceed without a customer's explicit, dated
approval. With an active grant, the request continues normally, but
``request.state.support_actor`` and ``request.state.support_grant_id``
are stamped onto the request so the AuditLogMiddleware records every
mutating action under that grant. The audit chain is the forensic
proof the customer keeps for their own auditors.

Storage mirrors invite_domains / dpa / security_contacts: append-only
JSONL with tombstones, last writer wins. No database required.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings


_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_LOCK = Lock()
_CACHE: dict[str, list["SupportGrant"]] | None = None
_CACHE_PATH: Path | None = None

# Allowed scopes. ``read`` permits only safe methods (GET, HEAD,
# OPTIONS); ``write`` permits any method. The auth layer enforces
# the mapping; this module just rejects bad strings at create time.
ALLOWED_SCOPES = ("read", "write")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Maximum lifetime of any single grant. Enterprise buyers expect
# vendor access windows to be short by default; an owner who needs a
# longer one can re-grant rather than mint a grant that outlives the
# incident. 7 days mirrors what most SOC2 auditors want to see for
# vendor break-glass access without becoming operationally annoying.
MAX_GRANT_SECONDS = 7 * 24 * 3600

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class SupportGrant:
    id: str
    tenant_id: str
    support_actor: str
    scope: str
    reason: str
    created_at: float
    expires_at: float
    created_by: str
    revoked_at: float | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None

    def is_active(self, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        if self.revoked_at is not None:
            return False
        if t >= self.expires_at:
            return False
        return True

    def permits_method(self, method: str) -> bool:
        if self.scope == "write":
            return True
        return method.upper() in SAFE_METHODS

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "support_actor": self.support_actor,
            "scope": self.scope,
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
        }
        if self.revoked_at is not None:
            out["revoked_at"] = self.revoked_at
            out["revoked_by"] = self.revoked_by
            out["revoke_reason"] = self.revoke_reason
        return out


def _new_id() -> str:
    return "sg_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _path() -> Path:
    return Path(get_settings().support_access_path)


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _validate_email(value: str) -> str:
    v = value.strip().lower()
    if not _EMAIL_RE.match(v):
        raise ValueError("support_actor must be a valid email address")
    return v


def _load_locked() -> dict[str, list[SupportGrant]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    by_id: dict[tuple[str, str], SupportGrant] = {}
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
                tenant = str(row.get("tenant_id") or "default")
                gid = row.get("id")
                if not gid:
                    continue
                key = (tenant, str(gid))
                if row.get("_revoke"):
                    existing = by_id.get(key)
                    if existing is None:
                        continue
                    by_id[key] = SupportGrant(
                        id=existing.id,
                        tenant_id=existing.tenant_id,
                        support_actor=existing.support_actor,
                        scope=existing.scope,
                        reason=existing.reason,
                        created_at=existing.created_at,
                        expires_at=existing.expires_at,
                        created_by=existing.created_by,
                        revoked_at=float(row.get("revoked_at") or time.time()),
                        revoked_by=str(row.get("revoked_by") or ""),
                        revoke_reason=str(row.get("revoke_reason") or ""),
                    )
                    continue
                try:
                    grant = SupportGrant(
                        id=str(row["id"]),
                        tenant_id=tenant,
                        support_actor=str(row["support_actor"]),
                        scope=str(row.get("scope") or "read"),
                        reason=str(row.get("reason") or ""),
                        created_at=float(row.get("created_at") or 0.0),
                        expires_at=float(row.get("expires_at") or 0.0),
                        created_by=str(row.get("created_by") or ""),
                    )
                except (KeyError, ValueError):
                    continue
                by_id[key] = grant
    out: dict[str, list[SupportGrant]] = {}
    for (tenant, _gid), grant in by_id.items():
        out.setdefault(tenant, []).append(grant)
    for bucket in out.values():
        bucket.sort(key=lambda g: g.created_at, reverse=True)
    _CACHE = out
    _CACHE_PATH = p
    return out


def list_grants(tenant_id: str) -> list[SupportGrant]:
    with _LOCK:
        store = _load_locked()
        return list(store.get(tenant_id, []))


def list_active_grants(tenant_id: str, now: float | None = None) -> list[SupportGrant]:
    return [g for g in list_grants(tenant_id) if g.is_active(now)]


def get_grant(tenant_id: str, grant_id: str) -> SupportGrant | None:
    for g in list_grants(tenant_id):
        if g.id == grant_id:
            return g
    return None


def find_active_for_actor(
    tenant_id: str, support_actor: str, now: float | None = None
) -> SupportGrant | None:
    """Return the most-recent active grant for ``support_actor`` in
    ``tenant_id``, or None when no grant covers the moment.

    Grants are matched case-insensitively on the email since the
    create path lowercases the value at validation time. Multiple
    overlapping active grants for the same actor are legal (an owner
    might extend access by issuing a fresh one without revoking the
    older one); the most recently created wins so the scope and
    expiry surfaced to auth are the freshest customer intent.
    """
    actor = support_actor.strip().lower()
    if not actor:
        return None
    best: SupportGrant | None = None
    for g in list_grants(tenant_id):
        if g.support_actor != actor:
            continue
        if not g.is_active(now):
            continue
        if best is None or g.created_at > best.created_at:
            best = g
    return best


def create_grant(
    tenant_id: str,
    support_actor: str,
    scope: str,
    reason: str,
    ttl_seconds: int,
    created_by: str,
) -> SupportGrant:
    actor = _validate_email(support_actor)
    scope_norm = (scope or "").strip().lower()
    if scope_norm not in ALLOWED_SCOPES:
        raise ValueError(
            f"scope must be one of {', '.join(ALLOWED_SCOPES)}"
        )
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if ttl_seconds > MAX_GRANT_SECONDS:
        raise ValueError(
            f"ttl_seconds exceeds max {MAX_GRANT_SECONDS} (7 days)"
        )
    reason_norm = (reason or "").strip()[:500]
    if not reason_norm:
        raise ValueError("reason is required so the audit log is meaningful")
    now = time.time()
    grant = SupportGrant(
        id=_new_id(),
        tenant_id=tenant_id,
        support_actor=actor,
        scope=scope_norm,
        reason=reason_norm,
        created_at=now,
        expires_at=now + ttl_seconds,
        created_by=created_by,
    )
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        store = _load_locked()
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(grant.to_dict()) + "\n")
        store.setdefault(tenant_id, []).insert(0, grant)
    return grant


def revoke_grant(
    tenant_id: str,
    grant_id: str,
    revoked_by: str,
    reason: str = "",
) -> SupportGrant | None:
    p = _path()
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        target = next((g for g in bucket if g.id == grant_id), None)
        if target is None:
            return None
        if target.revoked_at is not None:
            # Idempotent: re-revoking a revoked grant returns it as-is
            # rather than appending a redundant tombstone.
            return target
        now = time.time()
        revoked = SupportGrant(
            id=target.id,
            tenant_id=target.tenant_id,
            support_actor=target.support_actor,
            scope=target.scope,
            reason=target.reason,
            created_at=target.created_at,
            expires_at=target.expires_at,
            created_by=target.created_by,
            revoked_at=now,
            revoked_by=revoked_by,
            revoke_reason=(reason or "").strip()[:500],
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": grant_id,
                "tenant_id": tenant_id,
                "_revoke": True,
                "revoked_at": now,
                "revoked_by": revoked_by,
                "revoke_reason": revoked.revoke_reason or "",
            }) + "\n")
        store[tenant_id] = [revoked if g.id == grant_id else g for g in bucket]
    return revoked
