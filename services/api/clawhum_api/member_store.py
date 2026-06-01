"""Human workspace members and pending invites.

Why this exists: API keys and PATs cover machine to machine and
self issued tokens, but enterprise buyers always ask "who has access
to this workspace, and how do I remove them when they leave?". This
module owns the seat roster: invited email, role, status, who invited
them, when they joined, and a one shot invite token the recipient
trades for membership.

Storage follows the same append only JSONL pattern as PATs and
webhooks so multi worker deployments stay correct under last writer
wins semantics, no database required. Each line is one event for one
member id; the latest record wins and ``deleted=True`` tombstones it.

Enforcement model: only the API auth layer can mint or mutate
members, and routes layer above this module gates the mutating
endpoints to actors holding the admin role plus a fresh MFA code.
This module is intentionally storage only so it stays trivially
testable; no FastAPI, no HTTP, no audit log writes (those happen at
the middleware layer that wraps every mutating request).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from clawhum_core.settings import get_settings

from .api_keys import ROLES

_LOCK = Lock()

# Member ids are short, unguessable, URL safe. The invite token carries
# the real entropy; the id is only used to look up which row to mutate.
_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
INVITE_TOKEN_PREFIX = "inv_"

# Permissive but not pathological. Real validation happens at the auth
# provider that owns identities; we only need to reject obvious garbage
# so the UI does not show "you invited &lt;script&gt;".
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

STATUS_INVITED = "invited"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
_STATUSES = frozenset({STATUS_INVITED, STATUS_ACTIVE, STATUS_REVOKED})


def _new_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LEN))


def new_invite_token() -> str:
    """Return a fresh invite token. Shown to the inviter exactly once."""
    return INVITE_TOKEN_PREFIX + secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


@dataclass(frozen=True)
class Member:
    id: str
    tenant_id: str
    email: str
    role: str  # one of ROLES
    status: str  # one of _STATUSES
    invited_by: str  # actor id from auth.request.state.api_key_name
    invited_at: float
    accepted_at: float  # 0.0 means not yet accepted
    invite_token_hash: str  # empty once accepted or revoked
    invite_expires_at: float  # 0.0 means never
    deleted: bool = False

    def is_invite_expired(self, now: float | None = None) -> bool:
        if self.invite_expires_at <= 0:
            return False
        return (now if now is not None else time.time()) >= self.invite_expires_at

    def public_dict(self) -> dict[str, Any]:
        """Shape returned to API clients. Never includes the token hash."""
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "invited_by": self.invited_by,
            "invited_at": self.invited_at,
            "accepted_at": self.accepted_at,
            "invite_expires_at": self.invite_expires_at,
        }


def _path() -> Path:
    p = Path(get_settings().members_path)
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


def _load_all() -> dict[str, Member]:
    """Rebuild the in memory roster from disk. Last writer wins."""
    path = _path()
    rows: dict[str, Member] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = rec.get("id")
            if not mid:
                continue
            try:
                m = Member(
                    id=mid,
                    tenant_id=rec.get("tenant_id", ""),
                    email=normalise_email(rec.get("email", "")),
                    role=rec.get("role", "reader"),
                    status=rec.get("status", STATUS_INVITED),
                    invited_by=rec.get("invited_by", ""),
                    invited_at=float(rec.get("invited_at", 0.0)),
                    accepted_at=float(rec.get("accepted_at", 0.0)),
                    invite_token_hash=rec.get("invite_token_hash", ""),
                    invite_expires_at=float(rec.get("invite_expires_at", 0.0)),
                    deleted=bool(rec.get("deleted", False)),
                )
            except (TypeError, ValueError):
                continue
            rows[mid] = m
    return rows


def list_for_tenant(tenant_id: str) -> list[Member]:
    """Active (non tombstoned) members and pending invites for one tenant."""
    rows = _load_all()
    out = [
        m for m in rows.values()
        if m.tenant_id == tenant_id and not m.deleted
    ]
    # Stable, useful order: active members first by email, invites last by age.
    out.sort(key=lambda m: (m.status != STATUS_ACTIVE, m.email, m.invited_at))
    return out


def get(member_id: str) -> Member | None:
    rows = _load_all()
    m = rows.get(member_id)
    if m is None or m.deleted:
        return None
    return m


def find_active_by_email(tenant_id: str, email: str) -> Member | None:
    """Used to prevent inviting an email that already has a seat."""
    email = normalise_email(email)
    for m in list_for_tenant(tenant_id):
        if m.email == email and m.status != STATUS_REVOKED:
            return m
    return None


def lookup_by_token(token: str) -> Member | None:
    """Resolve an invite token to its pending member, or None.

    Returns None for unknown, accepted, revoked, expired, or tombstoned
    invites so callers can return a uniform "invalid token" error without
    leaking which case applied.
    """
    if not token or not token.startswith(INVITE_TOKEN_PREFIX):
        return None
    target = hash_token(token)
    for m in _load_all().values():
        if m.deleted or m.status != STATUS_INVITED:
            continue
        if not m.invite_token_hash:
            continue
        if secrets.compare_digest(m.invite_token_hash, target):
            if m.is_invite_expired():
                return None
            return m
    return None


def invite(
    *,
    tenant_id: str,
    email: str,
    role: str,
    invited_by: str,
    ttl_hours: int | None = None,
    now: float | None = None,
) -> tuple[Member, str]:
    """Create a pending invite and return (member, plaintext_token).

    The plaintext token is shown to the inviter exactly once. Callers
    should hand it to the recipient out of band (email, Slack, copy
    paste). The hashed form is stored so a leaked log file cannot be
    replayed.
    """
    tenant_id = (tenant_id or "").strip().lower()
    if not tenant_id:
        raise ValueError("tenant_id is required")
    email = normalise_email(email)
    if not is_valid_email(email):
        raise ValueError("invalid email")
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    if find_active_by_email(tenant_id, email) is not None:
        raise ValueError("email already invited or member of workspace")

    # Per-workspace invite domain allowlist. Empty rule set is a no op
    # so existing tenants keep working unchanged. Lazy import avoids a
    # circular dependency at module load.
    from . import invite_domains
    invite_domains.assert_allowed(tenant_id, email)

    # Seat license check. Lazy import avoids a circular import:
    # seat_limit_store imports member_store.count_for_tenant.
    from . import seat_limit_store
    seat_limit_store.check_capacity(tenant_id)

    settings = get_settings()
    if ttl_hours is None:
        ttl_hours = settings.member_invite_ttl_hours
    now = time.time() if now is None else now
    expires_at = 0.0 if ttl_hours <= 0 else now + (ttl_hours * 3600.0)

    token = new_invite_token()
    member = Member(
        id=_new_id(),
        tenant_id=tenant_id,
        email=email,
        role=role,
        status=STATUS_INVITED,
        invited_by=invited_by or "unknown",
        invited_at=now,
        accepted_at=0.0,
        invite_token_hash=hash_token(token),
        invite_expires_at=expires_at,
    )
    _append(asdict(member))
    return member, token


def create_active(
    *,
    tenant_id: str,
    email: str,
    role: str,
    invited_by: str,
    now: float | None = None,
) -> Member:
    """Provision an already-active seat without an invite token.

    Used by domain auto-join: the workspace admin has pre-authorised
    any successful SSO sign in from the configured email domain, so
    no out-of-band invite hand-off is needed. The returned member is
    immediately ``STATUS_ACTIVE`` with ``accepted_at`` set to ``now``.
    Idempotent: if an active or invited member already exists for
    this email + tenant we return the existing record unchanged so
    repeat sign-ins do not balloon the audit log.
    """
    tenant_id = (tenant_id or "").strip().lower()
    if not tenant_id:
        raise ValueError("tenant_id is required")
    email = normalise_email(email)
    if not is_valid_email(email):
        raise ValueError("invalid email")
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    existing = find_active_by_email(tenant_id, email)
    if existing is not None:
        return existing
    # Per-workspace invite domain allowlist also gates SSO auto-join so
    # an admin cannot accidentally pre-authorise the wrong identity
    # source. Empty rule set is a no op.
    from . import invite_domains
    invite_domains.assert_allowed(tenant_id, email)
    # Seat license check applies to fresh SSO auto-join seats. Existing
    # rows are exempt because they already hold a seat.
    from . import seat_limit_store
    seat_limit_store.check_capacity(tenant_id)
    now = time.time() if now is None else now
    member = Member(
        id=_new_id(),
        tenant_id=tenant_id,
        email=email,
        role=role,
        status=STATUS_ACTIVE,
        invited_by=invited_by or "sso-auto-join",
        invited_at=now,
        accepted_at=now,
        invite_token_hash="",
        invite_expires_at=0.0,
    )
    _append(asdict(member))
    return member


def accept(token: str, *, now: float | None = None) -> Member:
    """Accept a pending invite. Raises ValueError on any failure path."""
    m = lookup_by_token(token)
    if m is None:
        raise ValueError("invalid or expired invite token")
    # Re-check the invite domain allowlist at accept time so a policy
    # tightened after the invite was issued is still enforced. Empty
    # rule set remains a no op for existing tenants.
    from . import invite_domains
    invite_domains.assert_allowed(m.tenant_id, m.email)
    now = time.time() if now is None else now
    accepted = Member(
        id=m.id,
        tenant_id=m.tenant_id,
        email=m.email,
        role=m.role,
        status=STATUS_ACTIVE,
        invited_by=m.invited_by,
        invited_at=m.invited_at,
        accepted_at=now,
        invite_token_hash="",  # one shot, never reusable
        invite_expires_at=0.0,
        deleted=False,
    )
    _append(asdict(accepted))
    return accepted


class LastAdminError(ValueError):
    """Raised when an operation would remove the last admin member.

    Enterprise buyers reject any workspace model that can be orphaned
    (no admin left, no human can rotate keys or invite anyone). Routes
    surface this as HTTP 409 Conflict so the UI can show a specific
    "promote someone else to admin first" message rather than the
    generic 400 used for invalid input.
    """


def _active_admin_ids(tenant_id: str) -> list[str]:
    """Ids of members who currently hold admin and have accepted their seat.

    Pending invites do not count: an unaccepted invite cannot rotate a
    key or invite anyone, so it does not protect against lockout. The
    list is used by the last-admin guard in revoke and update_role.
    """
    return [
        m.id for m in list_for_tenant(tenant_id)
        if m.status == STATUS_ACTIVE and m.role == "admin"
    ]


def update_role(member_id: str, *, role: str, tenant_id: str) -> Member:
    """Change a member's role. Refuses cross tenant moves.

    Raises ``LastAdminError`` when the change would demote the only
    remaining active admin member of the workspace, which would lock
    the tenant out of every admin-gated route (invites, key rotation,
    SSO config, residency).
    """
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    m = get(member_id)
    if m is None or m.tenant_id != tenant_id:
        raise ValueError("member not found")
    if m.status == STATUS_REVOKED:
        raise ValueError("cannot change role of revoked member")
    if (
        m.status == STATUS_ACTIVE
        and m.role == "admin"
        and role != "admin"
        and _active_admin_ids(tenant_id) == [m.id]
    ):
        raise LastAdminError(
            "cannot demote the last admin: promote another active member to admin first"
        )
    updated = Member(
        id=m.id,
        tenant_id=m.tenant_id,
        email=m.email,
        role=role,
        status=m.status,
        invited_by=m.invited_by,
        invited_at=m.invited_at,
        accepted_at=m.accepted_at,
        invite_token_hash=m.invite_token_hash,
        invite_expires_at=m.invite_expires_at,
        deleted=False,
    )
    _append(asdict(updated))
    return updated


def resend_invite(
    member_id: str,
    *,
    tenant_id: str,
    ttl_hours: int | None = None,
    now: float | None = None,
) -> tuple[Member, str]:
    """Rotate the invite token for a pending member and extend the TTL.

    Enterprise admins routinely need to re-send an invite (the original
    email was lost, the link expired, the recipient mis-typed it).
    Revoking and re-inviting works but loses the lifecycle history and
    forces the admin to retype the email. This helper mints a fresh
    one shot token bound to the same member id, invalidating the old
    token, and resets ``invited_at`` and the expiry clock.

    Refuses to operate on accepted, revoked, tombstoned, or
    cross-tenant rows; raises ``ValueError`` with a uniform message so
    the API layer cannot leak which case applied.
    """
    m = get(member_id)
    if m is None or m.tenant_id != tenant_id:
        raise ValueError("pending invite not found")
    if m.status != STATUS_INVITED:
        raise ValueError("member is not in invited status")

    settings = get_settings()
    if ttl_hours is None:
        ttl_hours = settings.member_invite_ttl_hours
    now = time.time() if now is None else now
    expires_at = 0.0 if ttl_hours <= 0 else now + (ttl_hours * 3600.0)

    token = new_invite_token()
    rotated = Member(
        id=m.id,
        tenant_id=m.tenant_id,
        email=m.email,
        role=m.role,
        status=STATUS_INVITED,
        invited_by=m.invited_by,
        invited_at=now,
        accepted_at=0.0,
        invite_token_hash=hash_token(token),
        invite_expires_at=expires_at,
        deleted=False,
    )
    _append(asdict(rotated))
    return rotated, token


def revoke(member_id: str, *, tenant_id: str) -> Member:
    """Tombstone a member or pending invite. Idempotent.

    The row is appended with ``deleted=True`` so the on disk log stays
    immutable and auditors can replay the full lifecycle. List queries
    skip tombstones.

    Raises ``LastAdminError`` when the target is the only remaining
    active admin member of the workspace. Pending invites and revoked
    rows are always safe to remove and bypass the guard.
    """
    m = get(member_id)
    if m is None or m.tenant_id != tenant_id:
        raise ValueError("member not found")
    if (
        m.status == STATUS_ACTIVE
        and m.role == "admin"
        and _active_admin_ids(tenant_id) == [m.id]
    ):
        raise LastAdminError(
            "cannot revoke the last admin: promote another active member to admin first"
        )
    tomb = Member(
        id=m.id,
        tenant_id=m.tenant_id,
        email=m.email,
        role=m.role,
        status=STATUS_REVOKED,
        invited_by=m.invited_by,
        invited_at=m.invited_at,
        accepted_at=m.accepted_at,
        invite_token_hash="",  # invalidate any in flight token
        invite_expires_at=0.0,
        deleted=True,
    )
    _append(asdict(tomb))
    return tomb


def list_expired_invites(tenant_id: str, *, now: float | None = None) -> list[Member]:
    """Pending invites whose ``invite_expires_at`` is in the past.

    Enterprise compliance reviewers expect a workspace owner to be
    able to identify and clear stale invite tokens that nobody ever
    accepted (typo'd email, recipient left the company). The token is
    already useless after the expiry clock elapses (``lookup_by_token``
    returns ``None``), but the row stays in the roster forever unless
    an admin revokes it row by row. This helper surfaces the backlog
    so the admin console and audit reports can show one number.

    Invites with ``invite_expires_at == 0`` never expire (operator opt
    out) and are intentionally excluded.
    """
    tenant_id = (tenant_id or "").strip().lower()
    if not tenant_id:
        return []
    t = time.time() if now is None else now
    return [
        m for m in list_for_tenant(tenant_id)
        if m.status == STATUS_INVITED
        and m.invite_expires_at > 0
        and t >= m.invite_expires_at
    ]


def purge_expired_invites(
    tenant_id: str,
    *,
    now: float | None = None,
) -> list[Member]:
    """Tombstone every expired pending invite for ``tenant_id``.

    Returns the list of rows that were tombstoned (post-purge view, so
    each entry has ``status=revoked`` and ``deleted=True``). Idempotent:
    re-running yields an empty list once the backlog is cleared. The
    last-admin guard is bypassed because pending invites never count
    as active admins.

    Mutations go through the same append-only log as ``revoke`` so the
    audit timeline still shows each invite reaching its terminal state.
    """
    targets = list_expired_invites(tenant_id, now=now)
    purged: list[Member] = []
    for m in targets:
        tomb = Member(
            id=m.id,
            tenant_id=m.tenant_id,
            email=m.email,
            role=m.role,
            status=STATUS_REVOKED,
            invited_by=m.invited_by,
            invited_at=m.invited_at,
            accepted_at=m.accepted_at,
            invite_token_hash="",
            invite_expires_at=0.0,
            deleted=True,
        )
        _append(asdict(tomb))
        purged.append(tomb)
    return purged


def reset_for_tests() -> None:
    """Truncate the on disk log. Tests only."""
    path = _path()
    with _LOCK:
        path.write_text("", encoding="utf-8")


def count_for_tenant(tenant_id: str) -> dict[str, int]:
    """Useful for the admin console: members, invites pending, total seats."""
    members = list_for_tenant(tenant_id)
    out = {"active": 0, "invited": 0, "revoked": 0}
    for m in members:
        out[m.status] = out.get(m.status, 0) + 1
    return out
