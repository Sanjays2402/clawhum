"""Personal access tokens (PATs) the user mints from the web UI.

Why this exists: until now, the only way to get an API key was to set
``CLAWHUM_API_KEYS`` and restart the server. That works for ops but
locks self-serve customers out. PATs solve that: a logged-in tenant
(authenticated via an existing key with the ``writer`` role) can mint
a token, name it, see when it was last used, and revoke it at any
time. The minted secret is shown exactly once and stored hashed, so
even a stolen log file does not leak credentials.

Storage follows the existing JSONL append-only pattern used by
webhooks/share/feedback. Each line is one event for one token; the
latest record per id wins, and ``deleted=True`` tombstones it. The
in-memory index is rebuilt from disk on first lookup and on every
mutation, so multiple workers stay correct (within last-writer-wins
semantics, which is what these tenant-local files already provide).

Tokens inherit the minter's tenant and a subset of their roles, so a
``writer`` cannot mint an ``admin`` token. Each token can optionally
carry its own per-minute rate limit; if 0, the server default applies.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from clawhum_core.settings import get_settings

from .api_keys import ROLES, SCOPES, normalise_scopes, scopes_allowed_for_roles

_LOCK = Lock()

# Token id is short and unguessable; the secret carries the real entropy.
_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
PAT_PREFIX = "pat_"


def _new_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LEN))


def new_secret() -> str:
    """Return a fresh PAT secret. Shown to the user exactly once."""
    return PAT_PREFIX + secrets.token_urlsafe(24)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def looks_like_pat(secret: str) -> bool:
    return bool(secret) and secret.startswith(PAT_PREFIX)


@dataclass(frozen=True)
class PAT:
    id: str
    tenant_id: str
    name: str
    roles: frozenset[str]
    rpm: int
    created_at: float
    last_used_at: float  # 0.0 means "never"
    secret_hash: str
    secret_hint: str  # last 4 chars, for the UI
    # Forensic breadcrumbs from the most recent successful auth using
    # this token. Empty string means "no use recorded yet" (either the
    # PAT was never used, or every use predates this field). We record
    # the client IP that auth.py resolved (X-Forwarded-For aware) plus
    # a truncated User-Agent so an operator can spot a leaked token
    # being driven from an unexpected host or library without having
    # to grep the audit log. UA is hard-capped at 200 chars so a
    # hostile client cannot bloat the JSONL file.
    last_used_ip: str = ""
    last_used_ua: str = ""
    deleted: bool = False
    expires_at: float = 0.0  # 0.0 means "never expires"
    # Fine-grained scopes layered on top of roles. An empty set means
    # "every scope this PAT's roles permit", which keeps PATs minted
    # before this field existed working without a migration.
    scopes: frozenset[str] = frozenset()
    # Rotation: when a token is rotated, the previous secret stays
    # valid for a short grace window so deployed clients can swap to
    # the new secret without downtime. Both fields are 0.0/"" when no
    # rotation is in flight.
    prior_secret_hash: str = ""
    prior_secret_hint: str = ""
    prior_secret_expires_at: float = 0.0
    # Per-PAT IP allowlist. Each entry is a CIDR string (e.g.
    # "203.0.113.0/24" or "2001:db8::/32"). Empty set means "no
    # restriction" so existing PATs minted before this field stay
    # usable from any IP. When non-empty, the client IP MUST match at
    # least one CIDR or the request is rejected with 403. This is a
    # per-credential narrowing layered on top of the workspace-wide
    # ip_allowlist; both must pass.
    ip_cidrs: frozenset[str] = frozenset()

    def prior_secret_active(self, now: float | None = None) -> bool:
        if not self.prior_secret_hash or self.prior_secret_expires_at <= 0:
            return False
        return (now if now is not None else time.time()) < self.prior_secret_expires_at

    def effective_scopes(self) -> frozenset[str]:
        if self.scopes:
            return self.scopes
        return scopes_allowed_for_roles(self.roles)

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at <= 0:
            return False
        return (now if now is not None else time.time()) >= self.expires_at


def _path() -> Path:
    p = get_settings().pat_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _iter_records() -> Iterable[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Skip torn writes; the next valid record still wins.
                continue


def _append(rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _from_record(rec: dict[str, Any]) -> PAT:
    roles_raw = rec.get("roles") or []
    roles = frozenset(r for r in roles_raw if r in ROLES)
    return PAT(
        id=rec["id"],
        tenant_id=rec["tenant_id"],
        name=rec.get("name", ""),
        roles=roles,
        rpm=int(rec.get("rpm", 0) or 0),
        created_at=float(rec.get("created_at", 0.0)),
        last_used_at=float(rec.get("last_used_at", 0.0)),
        secret_hash=rec.get("secret_hash", ""),
        secret_hint=rec.get("secret_hint", ""),
        last_used_ip=str(rec.get("last_used_ip", "") or ""),
        last_used_ua=str(rec.get("last_used_ua", "") or ""),
        deleted=bool(rec.get("deleted", False)),
        expires_at=float(rec.get("expires_at", 0.0) or 0.0),
        scopes=normalise_scopes(rec.get("scopes") or []),
        prior_secret_hash=str(rec.get("prior_secret_hash", "") or ""),
        prior_secret_hint=str(rec.get("prior_secret_hint", "") or ""),
        prior_secret_expires_at=float(rec.get("prior_secret_expires_at", 0.0) or 0.0),
        ip_cidrs=normalise_cidrs(rec.get("ip_cidrs") or []),
    )


def _reduce() -> dict[str, PAT]:
    """Walk the log, keeping the latest record per id."""
    by_id: dict[str, dict[str, Any]] = {}
    for rec in _iter_records():
        if "id" not in rec:
            continue
        by_id[rec["id"]] = rec
    out: dict[str, PAT] = {}
    for rid, rec in by_id.items():
        out[rid] = _from_record(rec)
    return out


def live_for_tenant(tenant_id: str) -> list[PAT]:
    return [
        t
        for t in _reduce().values()
        if not t.deleted and t.tenant_id == tenant_id
    ]


def lookup_by_secret(secret: str) -> PAT | None:
    """Return the live PAT matching this secret, or None.

    O(n) over live tokens. Fine for the realistic ceiling (hundreds);
    revisit if a single tenant ever mints thousands.
    """
    if not looks_like_pat(secret):
        return None
    h = hash_secret(secret)
    now = time.time()
    for tok in _reduce().values():
        if tok.deleted:
            continue
        if hmac_compare(tok.secret_hash, h):
            if tok.is_expired(now):
                return None
            return tok
        # Accept the previous secret while the rotation grace window
        # is still open. Once the window closes, the prior hash is
        # treated as if it were never minted.
        if tok.prior_secret_active(now) and hmac_compare(tok.prior_secret_hash, h):
            if tok.is_expired(now):
                return None
            return tok
    return None


def hmac_compare(a: str, b: str) -> bool:
    """Constant time string compare."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


def resolve_expiry(
    *,
    requested_days: int | None,
    now: float | None = None,
) -> float:
    """Translate a TTL in days into an absolute epoch seconds expiry.

    Caller passes ``None`` to mean "use the deployment default". Passing
    ``0`` explicitly means "never expires" and is honoured only when the
    operator has not set a hard cap via ``pat_max_ttl_days``. Any
    positive request is clamped to the cap so callers cannot escape the
    workspace policy. Returns ``0.0`` for a non-expiring token.
    """
    s = get_settings()
    cap = max(0, int(s.pat_max_ttl_days or 0))
    default = max(0, int(s.pat_default_ttl_days or 0))
    if requested_days is None:
        days = default
    else:
        days = max(0, int(requested_days))
    if cap > 0:
        # Hard cap: clamp both explicit asks and the default to the
        # operator-defined ceiling. A 0-day ask becomes the cap so
        # "never" is never honoured when a cap is set.
        if days <= 0 or days > cap:
            days = cap
    if days <= 0:
        return 0.0
    base = now if now is not None else time.time()
    return base + days * 86400.0


def create(
    *,
    tenant_id: str,
    name: str,
    roles: frozenset[str],
    rpm: int = 0,
    expires_in_days: int | None = None,
    scopes: frozenset[str] | None = None,
    ip_cidrs: Iterable[str] | None = None,
) -> tuple[PAT, str]:
    """Mint a new PAT. Returns (record, plaintext_secret_shown_once).

    ``scopes`` is an optional least-privilege narrowing of what the
    token may do. When supplied, it is clamped to the set the caller's
    roles already permit; this prevents a `writer` from minting a PAT
    that carries `admin` scope by stuffing it into the body. An empty
    or omitted scope set means "every scope the roles imply" so the
    common case (mint a writer PAT, use it everywhere) stays one click.
    """
    safe_roles = frozenset(r for r in roles if r in ROLES) or frozenset({"reader"})
    safe_scopes = normalise_scopes(scopes) & scopes_allowed_for_roles(safe_roles)
    safe_cidrs = normalise_cidrs(ip_cidrs)
    secret = new_secret()
    now = time.time()
    expires_at = resolve_expiry(requested_days=expires_in_days, now=now)
    # Enforce the workspace session policy cap on PAT lifetime. Import
    # inline to avoid a circular dependency: sessions does not import
    # pat_store, and we deliberately keep that direction one-way.
    try:
        from . import sessions as _sessions
        expires_at = _sessions.cap_pat_expiry(tenant_id, expires_at, now=now)
    except Exception:
        pass
    rec = {
        "id": _new_id(),
        "tenant_id": tenant_id,
        "name": (name or "").strip()[:64] or "untitled",
        "roles": sorted(safe_roles),
        "rpm": max(0, int(rpm or 0)),
        "created_at": now,
        "last_used_at": 0.0,
        "secret_hash": hash_secret(secret),
        "secret_hint": secret[-4:],
        "last_used_ip": "",
        "last_used_ua": "",
        "deleted": False,
        "expires_at": expires_at,
        "scopes": sorted(safe_scopes),
        "prior_secret_hash": "",
        "prior_secret_hint": "",
        "prior_secret_expires_at": 0.0,
        "ip_cidrs": sorted(safe_cidrs),
    }
    _append(rec)
    return _from_record(rec), secret


# Default grace window during which the previous secret keeps working
# after a rotation. Long enough for a rolling deploy to pick up the
# new value, short enough that a leaked old secret is not useful for
# long. Clamped per call by the operator via `pat_rotation_max_grace_minutes`.
_DEFAULT_ROTATION_GRACE_MINUTES = 60


def rotate(
    *,
    tenant_id: str,
    pat_id: str,
    grace_minutes: int | None = None,
) -> tuple[PAT, str] | None:
    """Rotate a PAT in place. Returns (record, new_plaintext_secret) or None.

    The token id, name, roles, scopes, rpm, expiry, and last-used
    timestamp all carry over. A fresh secret is generated and shown
    once. The previous secret keeps authenticating for ``grace_minutes``
    so existing deployments can be updated without a downtime window.
    Set ``grace_minutes`` to 0 to revoke the old secret immediately
    ("emergency rotate"). The grace is clamped to the operator-defined
    ceiling via ``pat_rotation_max_grace_minutes`` so a workspace owner
    cannot extend it indefinitely.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    s = get_settings()
    cap = max(0, int(getattr(s, "pat_rotation_max_grace_minutes", _DEFAULT_ROTATION_GRACE_MINUTES) or 0))
    if grace_minutes is None:
        grace = _DEFAULT_ROTATION_GRACE_MINUTES
    else:
        grace = max(0, int(grace_minutes))
    if cap > 0:
        grace = min(grace, cap)
    now = time.time()
    new_secret = new_secret_token()
    grace_expires = (now + grace * 60.0) if grace > 0 else 0.0
    rec = {
        "id": current.id,
        "tenant_id": current.tenant_id,
        "name": current.name,
        "roles": sorted(current.roles),
        "rpm": current.rpm,
        # created_at is reset on rotation so the workspace
        # max_pat_age_minutes force-rotation policy treats a freshly
        # rotated token as freshly minted. Without this, rotating
        # would not satisfy a SOC2 "rotate every N days" control
        # because the new secret would inherit the old age.
        "created_at": now,
        "last_used_at": current.last_used_at,
        "secret_hash": hash_secret(new_secret),
        "secret_hint": new_secret[-4:],
        "last_used_ip": current.last_used_ip,
        "last_used_ua": current.last_used_ua,
        "deleted": False,
        "expires_at": current.expires_at,
        "scopes": sorted(current.scopes),
        "prior_secret_hash": current.secret_hash if grace > 0 else "",
        "prior_secret_hint": current.secret_hint if grace > 0 else "",
        "prior_secret_expires_at": grace_expires,
        "ip_cidrs": sorted(current.ip_cidrs),
        "rotated_at": now,
    }
    _append(rec)
    return _from_record(rec), new_secret


# Alias kept so the symbol `new_secret` (already used externally) is
# preserved while `rotate` uses an explicit name.
new_secret_token = new_secret


def revoke(*, tenant_id: str, pat_id: str) -> bool:
    """Tombstone a PAT. Returns False if not owned by tenant or missing."""
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return False
    rec = {
        "id": current.id,
        "tenant_id": current.tenant_id,
        "name": current.name,
        "roles": sorted(current.roles),
        "rpm": current.rpm,
        "created_at": current.created_at,
        "last_used_at": current.last_used_at,
        "secret_hash": current.secret_hash,
        "secret_hint": current.secret_hint,
        "last_used_ip": current.last_used_ip,
        "last_used_ua": current.last_used_ua,
        "deleted": True,
        "expires_at": current.expires_at,
        "scopes": sorted(current.scopes),
        "prior_secret_hash": "",
        "prior_secret_hint": "",
        "prior_secret_expires_at": 0.0,
        "ip_cidrs": sorted(current.ip_cidrs),
        "revoked_at": time.time(),
    }
    _append(rec)
    return True


def revoke_all_for_tenant(
    *,
    tenant_id: str,
    except_pat_id: str | None = None,
) -> list[str]:
    """Tombstone every live PAT owned by ``tenant_id``.

    Optionally preserves a single ``except_pat_id`` so the caller can
    invalidate every credential except the one they are currently
    holding (the typical incident-response shape: "sign me out
    everywhere else"). Returns the list of ids that were revoked, in
    the order they were processed, so the caller can echo a count in
    the API response and the audit log can list the targets.

    Idempotent: calling twice yields an empty list the second time.
    """
    out: list[str] = []
    for pat in live_for_tenant(tenant_id):
        if except_pat_id and pat.id == except_pat_id:
            continue
        if revoke(tenant_id=tenant_id, pat_id=pat.id):
            out.append(pat.id)
    return out


_UA_MAX_LEN = 200


def _trim_ua(ua: str | None) -> str:
    """Bound the recorded User-Agent.

    Clients control this header so we strip control bytes and cap the
    length to keep one JSONL line bounded regardless of input.
    """
    if not ua:
        return ""
    cleaned = "".join(ch for ch in str(ua) if ch >= " " and ch != "\x7f")
    return cleaned[:_UA_MAX_LEN]


def touch_last_used(
    pat_id: str,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append a no-op record that bumps last_used_at. Best-effort.

    When ``client_ip`` or ``user_agent`` is provided, those fields are
    refreshed too so the keys UI and incident-response tooling can
    show *where* a token was last used from. Empty/None values reuse
    whatever was previously recorded, so a misbehaving proxy that
    strips XFF on one request does not erase the last good signal.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted:
        return
    new_ip = (client_ip or "").strip() or current.last_used_ip
    new_ua = _trim_ua(user_agent) or current.last_used_ua
    rec = {
        "id": current.id,
        "tenant_id": current.tenant_id,
        "name": current.name,
        "roles": sorted(current.roles),
        "rpm": current.rpm,
        "created_at": current.created_at,
        "last_used_at": time.time(),
        "secret_hash": current.secret_hash,
        "secret_hint": current.secret_hint,
        "last_used_ip": new_ip,
        "last_used_ua": new_ua,
        "deleted": False,
        "expires_at": current.expires_at,
        "scopes": sorted(current.scopes),
        "prior_secret_hash": current.prior_secret_hash,
        "prior_secret_hint": current.prior_secret_hint,
        "prior_secret_expires_at": current.prior_secret_expires_at,
        "ip_cidrs": sorted(current.ip_cidrs),
    }
    _append(rec)


def public_view(p: PAT) -> dict[str, Any]:
    # Surface workspace force-rotation policy state for this PAT so
    # /settings/keys can badge tokens that are about to age out or
    # already rejected by auth. Computed on read, never stored.
    try:
        from . import sessions as _sessions
        _policy = _sessions.get_policy(p.tenant_id or "")
        _remaining = _sessions.pat_age_seconds_remaining(
            created_at=p.created_at, policy=_policy
        )
    except Exception:
        _policy = None
        _remaining = None
    _aged = _remaining is not None and _remaining <= 0
    return {
        "id": p.id,
        "name": p.name,
        "roles": sorted(p.roles),
        "rpm": p.rpm,
        "created_at": p.created_at,
        "last_used_at": p.last_used_at,
        "last_used_ip": p.last_used_ip,
        "last_used_ua": p.last_used_ua,
        "secret_hint": p.secret_hint,
        "expires_at": p.expires_at,
        "expired": p.is_expired(),
        "scopes": sorted(p.scopes),
        "effective_scopes": sorted(p.effective_scopes()),
        "prior_secret_hint": p.prior_secret_hint,
        "prior_secret_expires_at": p.prior_secret_expires_at,
        "rotation_active": p.prior_secret_active(),
        "ip_cidrs": sorted(p.ip_cidrs),
        "max_age_minutes": (
            _policy.max_pat_age_minutes if _policy is not None else 0
        ),
        "age_seconds_remaining": (
            None if _remaining is None else max(int(_remaining), -1)
        ),
        "aged_out": _aged,
    }


def normalise_cidrs(raw: Iterable[str] | None) -> frozenset[str]:
    """Validate and canonicalise a list of CIDR strings.

    Each entry is parsed with ``strict=False`` so a host address like
    ``192.0.2.5/32`` round-trips cleanly and ``192.168.1.5/24`` becomes
    ``192.168.1.0/24``. Anything unparseable raises ``ValueError`` so
    the caller surfaces a 400; duplicates are collapsed. Returns an
    empty frozenset when ``raw`` is None or empty.
    """
    if not raw:
        return frozenset()
    out: set[str] = set()
    for entry in raw:
        s = str(entry or "").strip()
        if not s:
            continue
        # Raises ValueError on malformed input; let it propagate.
        net = ipaddress.ip_network(s, strict=False)
        out.add(str(net))
        if len(out) > 64:
            raise ValueError("too many cidrs (max 64 per pat)")
    return frozenset(out)


def ip_in_cidrs(client_ip: str, cidrs: Iterable[str]) -> bool:
    """Return True when ``client_ip`` matches any CIDR in ``cidrs``.

    Empty ``cidrs`` means "no restriction" and is always True. An
    unparseable client IP is always False so a credential constrained
    by IP cannot be used when the peer address is unknown.
    """
    cidr_list = list(cidrs)
    if not cidr_list:
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except (ValueError, TypeError):
        return False
    for entry in cidr_list:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def set_ip_cidrs(
    *, tenant_id: str, pat_id: str, cidrs: Iterable[str]
) -> PAT | None:
    """Replace the per-PAT IP allowlist. Returns updated PAT or None.

    Empty list clears the restriction (token usable from any IP).
    Caller is responsible for validating ``cidrs`` upstream so a 400
    is returned to the user instead of a 500 from the parser; we
    re-normalise here as a defence-in-depth.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe = normalise_cidrs(list(cidrs))
    rec = {
        "id": current.id,
        "tenant_id": current.tenant_id,
        "name": current.name,
        "roles": sorted(current.roles),
        "rpm": current.rpm,
        "created_at": current.created_at,
        "last_used_at": current.last_used_at,
        "secret_hash": current.secret_hash,
        "secret_hint": current.secret_hint,
        "last_used_ip": current.last_used_ip,
        "last_used_ua": current.last_used_ua,
        "deleted": False,
        "expires_at": current.expires_at,
        "scopes": sorted(current.scopes),
        "prior_secret_hash": current.prior_secret_hash,
        "prior_secret_hint": current.prior_secret_hint,
        "prior_secret_expires_at": current.prior_secret_expires_at,
        "ip_cidrs": sorted(safe),
        "ip_updated_at": time.time(),
    }
    _append(rec)
    return _from_record(rec)
