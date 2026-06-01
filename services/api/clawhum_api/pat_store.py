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


def new_secret(*, tenant_prefix: str = "") -> str:
    """Return a fresh PAT secret. Shown to the user exactly once.

    When ``tenant_prefix`` is supplied, the minted secret is shaped as
    ``pat_<tenant_prefix>_<random>`` so workspace-scoped secret
    scanners can attribute a leaked token to the right tenant. Empty
    ``tenant_prefix`` keeps the legacy ``pat_<random>`` shape.
    """
    body = secrets.token_urlsafe(24)
    if tenant_prefix:
        return f"{PAT_PREFIX}{tenant_prefix}_{body}"
    return PAT_PREFIX + body


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
    # Per-PAT URL path-prefix allowlist. Each entry is a string that
    # the request path must startwith (with a '/' boundary) for the
    # token to be accepted. Empty set means "no restriction" so PATs
    # minted before this field stay usable on any route. When non-
    # empty, requests whose path does not match at least one prefix
    # (or the always-allowed carve-out) are rejected with 403. This
    # is a least-privilege narrowing layered on top of scopes:
    # scopes decide *what* a token can do, path prefixes decide
    # *where* it can reach. /me, /mfa, /sessions, and /keys/policy
    # are always reachable so a pinned token can rotate itself.
    path_prefixes: frozenset[str] = frozenset()
    # Per-PAT trusted-device strict mode. When True, the auth layer
    # rejects any request whose computed device fingerprint is not
    # on the approved list for this PAT; the unknown device is
    # recorded as ``pending`` so the workspace owner can review it
    # from /settings/keys. When False (default) the PAT is usable
    # from any device. See pat_trusted_devices.py for fingerprint
    # computation and storage details.
    require_device_approval: bool = False
    # Per-PAT HTTP method allowlist. Each entry is an uppercase HTTP
    # verb (GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS). Empty set
    # means "no restriction" so existing PATs minted before this
    # field stay usable with any verb. When non-empty, requests whose
    # method is not in the set are rejected with HTTP 405. This is
    # the cleanest way to mint a true read-only token: pin a PAT to
    # {GET, HEAD} and a leak cannot mutate state regardless of
    # scopes or path pinning. Layered on top of scopes (what) and
    # path_prefixes (where); this gates *how*.
    http_methods: frozenset[str] = frozenset()
    # Per-PAT usage windows. Each entry is a string of the form
    # ``<days>:HH:MM-HH:MM`` where ``<days>`` is one of ``mon``,
    # ``tue``, ``wed``, ``thu``, ``fri``, ``sat``, ``sun``, ``all``,
    # or a dash range like ``mon-fri``. Times are 24h UTC. A request
    # is accepted when the current UTC weekday and HH:MM fall inside
    # at least one window. Empty set means "no restriction" so
    # existing PATs minted before this field shipped stay usable
    # 24x7. Windows shrink the leak blast-radius for tokens that
    # only need to work during business hours or for a nightly job:
    # a CI key pinned to ``mon-fri:06:00-20:00`` becomes useless to
    # an attacker after 8pm Pacific without the workspace owner
    # having to rotate.
    usage_windows: frozenset[str] = frozenset()
    # Per-PAT owner contact email. Optional free-text (validated as a
    # syntactically reasonable address when non-empty). Surfacing this
    # in the workspace key inventory lets a CISO answer the standard
    # SOC2 CC6.1 ownership question ("who do we page if this credential
    # is leaked?") without crawling chat history. Default empty so
    # every PAT minted before this field shipped keeps working.
    owner_email: str = ""
    # Free-text purpose / runbook note for the credential. Procurement
    # and SOC2 reviewers ask "what does this token do?" during access
    # reviews; storing the answer next to the credential keeps the
    # workspace owner from grepping ticket history. Capped at 200
    # chars, control characters stripped. Default empty so PATs minted
    # before this field shipped keep working unchanged.
    description: str = ""

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
        path_prefixes=normalise_path_prefixes(rec.get("path_prefixes") or []),
        require_device_approval=bool(rec.get("require_device_approval", False)),
        http_methods=normalise_http_methods(rec.get("http_methods") or []),
        usage_windows=normalise_usage_windows(rec.get("usage_windows") or []),
        owner_email=normalise_owner_email(rec.get("owner_email") or "", allow_blank=True),
        description=normalise_description(rec.get("description") or ""),
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
    path_prefixes: Iterable[str] | None = None,
    http_methods: Iterable[str] | None = None,
    usage_windows: Iterable[str] | None = None,
    owner_email: str | None = None,
    description: str | None = None,
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
    requested_scopes = normalise_scopes(scopes)
    # Workspace scope policy: when an admin has pinned the set of scopes
    # this workspace may ever mint, any request for a scope outside it
    # is a hard error so the operator notices instead of silently
    # getting a narrower token than they asked for. Import inline to
    # keep pat_store dependency free at module load.
    try:
        from . import scope_policy as _scope_policy
        _scope_policy.assert_allowed(tenant_id, requested_scopes)
    except ImportError:
        pass
    safe_scopes = requested_scopes & scopes_allowed_for_roles(safe_roles)
    # When the caller didn't ask for explicit scopes the PAT inherits
    # "every scope the roles allow" lazily via effective_scopes(). The
    # workspace policy still applies at *use* time through
    # scopes_allowed_for_roles ∩ allowed_scopes(tenant), but we also
    # materialise the intersection here when a policy is active so the
    # stored token is honest about what it can do and the dashboard's
    # "effective scopes" column does not lie.
    try:
        from . import scope_policy as _scope_policy
        if not safe_scopes and _scope_policy.has_policy(tenant_id):
            safe_scopes = scopes_allowed_for_roles(safe_roles) & _scope_policy.allowed_scopes(tenant_id)
    except ImportError:
        pass
    safe_cidrs = normalise_cidrs(ip_cidrs)
    safe_prefixes = normalise_path_prefixes(path_prefixes)
    safe_methods = normalise_http_methods(http_methods)
    safe_windows = normalise_usage_windows(usage_windows)
    safe_owner_email = normalise_owner_email(owner_email or "", allow_blank=True)
    safe_description = normalise_description(description or "")
    # Workspace concurrent-PAT cap: when an admin has pinned
    # ``max_active`` for this workspace, any mint that would push the
    # live token count over the cap is rejected here so the operator
    # sees a structured failure instead of silently sprawling.
    try:
        from . import pat_concurrency as _pat_concurrency
        _pat_concurrency.assert_capacity(tenant_id)
    except ImportError:
        pass
    # Workspace PAT secret prefix policy: when an admin has set a
    # custom prefix, every newly minted secret is shaped
    # ``pat_<prefix>_<random>`` so the workspace's secret scanner
    # can claim leaked tokens unambiguously. Existing tokens are
    # untouched because rewriting them would break live deployments.
    tenant_prefix = ""
    try:
        from . import pat_secret_prefix as _pat_secret_prefix
        tenant_prefix = _pat_secret_prefix.get_prefix(tenant_id)
    except ImportError:
        pass
    secret = new_secret(tenant_prefix=tenant_prefix)
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
        "path_prefixes": sorted(safe_prefixes),
        "require_device_approval": False,
        "http_methods": sorted(safe_methods),
        "usage_windows": sorted(safe_windows),
        "owner_email": safe_owner_email,
        "description": safe_description,
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
    # Honour the workspace PAT secret prefix policy on rotation too,
    # so a rotated secret carries the same tenant prefix shape new
    # mints get. If a tenant cleared their prefix policy after the
    # original mint, rotation falls back to the legacy ``pat_``
    # shape with no migration drama.
    tenant_prefix = ""
    try:
        from . import pat_secret_prefix as _pat_secret_prefix
        tenant_prefix = _pat_secret_prefix.get_prefix(current.tenant_id)
    except ImportError:
        pass
    new_secret = new_secret_token(tenant_prefix=tenant_prefix)
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
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "rotated_at": now,
        "owner_email": current.owner_email,
        "description": current.description,
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
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "revoked_at": time.time(),
        "owner_email": current.owner_email,
        "description": current.description,
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
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "owner_email": current.owner_email,
        "description": current.description,
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
        _idle_remaining = _sessions.pat_idle_seconds_remaining(
            last_used_at=p.last_used_at,
            created_at=p.created_at,
            policy=_policy,
        )
    except Exception:
        _policy = None
        _remaining = None
        _idle_remaining = None
    _aged = _remaining is not None and _remaining <= 0
    _idle_revoked = _idle_remaining is not None and _idle_remaining <= 0
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
        "path_prefixes": sorted(p.path_prefixes),
        "require_device_approval": p.require_device_approval,
        "http_methods": sorted(p.http_methods),
        "usage_windows": sorted(p.usage_windows),
        "owner_email": p.owner_email,
        "description": p.description,
        "max_age_minutes": (
            _policy.max_pat_age_minutes if _policy is not None else 0
        ),
        "age_seconds_remaining": (
            None if _remaining is None else max(int(_remaining), -1)
        ),
        "aged_out": _aged,
        "max_idle_minutes": (
            _policy.max_pat_idle_minutes if _policy is not None else 0
        ),
        "idle_seconds_remaining": (
            None if _idle_remaining is None else max(int(_idle_remaining), -1)
        ),
        "idle_revoked": _idle_revoked,
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


# Paths that stay reachable even when a PAT has a path-prefix
# allowlist set. Without these, a token pinned to '/match' could not
# introspect itself, rotate its own secret, or read its workspace
# policy, which would make path pinning a footgun. Keep this list
# short and read-only; never put a mutating route here.
_PAT_PATH_ALWAYS_ALLOWED: frozenset[str] = frozenset({
    "/me",
    "/mfa",
    "/sessions",
    "/keys/policy",
})


def normalise_path_prefixes(raw: Iterable[str] | None) -> frozenset[str]:
    """Validate and canonicalise a list of URL path prefixes.

    Rules:
    * Each entry must start with '/'.
    * No spaces, no '?', no '#', no '..', no NUL.
    * Stripped of any trailing '/' so '/match/' and '/match' collapse.
    * Length capped per entry and total list size capped (defence in
      depth on top of the route's pydantic ``max_length``).
    Raises ``ValueError`` on malformed input so the caller surfaces a
    400 instead of a 500. Returns an empty frozenset when ``raw`` is
    None or empty.
    """
    if not raw:
        return frozenset()
    out: set[str] = set()
    for entry in raw:
        s = str(entry or "").strip()
        if not s:
            continue
        if not s.startswith("/"):
            raise ValueError(f"prefix must start with '/': {s!r}")
        if any(ch in s for ch in (" ", "\t", "\n", "\r", "?", "#", "\x00")):
            raise ValueError(f"prefix contains forbidden char: {s!r}")
        if ".." in s:
            raise ValueError(f"prefix may not contain '..': {s!r}")
        if len(s) > 200:
            raise ValueError("prefix too long (max 200 chars)")
        # Collapse trailing slash so '/match/' == '/match'. We keep the
        # root '/' as a degenerate "allow everything" entry; harmless
        # because that means the owner explicitly opted out of pinning.
        if s != "/" and s.endswith("/"):
            s = s.rstrip("/") or "/"
        out.add(s)
        if len(out) > 32:
            raise ValueError("too many path prefixes (max 32 per pat)")
    return frozenset(out)


def path_matches_allowlist(path: str, prefixes: Iterable[str]) -> bool:
    """Return True when ``path`` is permitted under ``prefixes``.

    Matching is exact-or-'/'-bounded so '/match' allows '/match' and
    '/match/anything' but never '/matches' or '/matchbox'. The root
    '/' prefix degenerates to "match anything". The always-allowed
    carve-out (``_PAT_PATH_ALWAYS_ALLOWED``) is checked first so a
    pinned token can still hit '/me', '/mfa', '/sessions', and
    '/keys/policy'. An empty allowlist also returns True so callers
    can use this as the sole gate.
    """
    p = path or "/"
    # Strip a trailing slash so '/match/' matches a '/match' rule;
    # never strip the root.
    norm = p.rstrip("/") or "/"
    for always in _PAT_PATH_ALWAYS_ALLOWED:
        if norm == always or norm.startswith(always + "/"):
            return True
    prefix_set = frozenset(prefixes)
    if not prefix_set:
        return True
    for rule in prefix_set:
        if rule == "/":
            return True
        if norm == rule or norm.startswith(rule + "/"):
            return True
    return False


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


def set_path_prefixes(
    *, tenant_id: str, pat_id: str, prefixes: Iterable[str]
) -> PAT | None:
    """Replace the per-PAT path-prefix allowlist. Returns updated PAT
    or None when the id is unknown or owned by another tenant. Empty
    list clears the restriction (token usable on any route).
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe = normalise_path_prefixes(list(prefixes))
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(safe),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "path_prefixes_updated_at": time.time(),
        "owner_email": current.owner_email,
        "description": current.description,
    }
    _append(rec)
    return _from_record(rec)


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
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "ip_updated_at": time.time(),
        "owner_email": current.owner_email,
        "description": current.description,
    }
    _append(rec)
    return _from_record(rec)


def set_require_device_approval(
    *, tenant_id: str, pat_id: str, required: bool
) -> PAT | None:
    """Toggle the per-PAT trusted-device strict mode.

    Returns the updated PAT or None when the id is unknown or owned
    by another tenant. No-op when the bit is already in the requested
    state so the audit log does not record useless churn.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    if bool(current.require_device_approval) == bool(required):
        return current
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": bool(required),
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "device_policy_updated_at": time.time(),
        "owner_email": current.owner_email,
        "description": current.description,
    }
    _append(rec)
    return _from_record(rec)


def set_path_prefixes(
    *, tenant_id: str, pat_id: str, prefixes: Iterable[str]
) -> PAT | None:
    """Replace the per-PAT URL path-prefix allowlist.

    Empty list clears the restriction (token usable on any route).
    Raises ``ValueError`` when any entry is malformed so the caller
    surfaces 400; returns None when the token is unknown or owned by
    another tenant.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe = normalise_path_prefixes(list(prefixes))
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(safe),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "path_updated_at": time.time(),
        "owner_email": current.owner_email,
        "description": current.description,
    }
    _append(rec)
    return _from_record(rec)


_VALID_HTTP_METHODS: frozenset[str] = frozenset({
    "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
})


def normalise_http_methods(raw: Iterable[str] | None) -> frozenset[str]:
    """Validate and canonicalise a list of HTTP method names.

    Each entry is upper-cased and checked against the set of methods
    the API actually serves. Unknown verbs raise ``ValueError`` so a
    typo (\"GETS\", \"POSTT\") surfaces a 400 instead of silently
    becoming a no-op fence. Duplicates collapse. An empty list (or
    None) is returned as the empty frozenset which the enforcer
    treats as \"no restriction\".
    """
    if not raw:
        return frozenset()
    out: set[str] = set()
    for entry in raw:
        s = str(entry or "").strip().upper()
        if not s:
            continue
        if s not in _VALID_HTTP_METHODS:
            raise ValueError(
                f"unknown http method: {s!r} (allowed: "
                + ", ".join(sorted(_VALID_HTTP_METHODS))
                + ")"
            )
        out.add(s)
    return frozenset(out)


def method_matches_allowlist(method: str, methods: Iterable[str]) -> bool:
    """Return True when ``method`` is permitted under ``methods``.

    Empty ``methods`` means \"no restriction\" and is always True so
    the field stays backward compatible with PATs minted before it
    existed. HEAD is always implicitly allowed when GET is, because
    HTTP requires HEAD to mirror GET semantics and refusing HEAD
    while permitting GET would break well-behaved clients (caches,
    monitoring probes) that issue HEAD as a cheap precheck.
    """
    method_set = frozenset(m.upper() for m in methods if m)
    if not method_set:
        return True
    m = (method or "").upper()
    if m in method_set:
        return True
    if m == "HEAD" and "GET" in method_set:
        return True
    return False


def set_http_methods(
    *, tenant_id: str, pat_id: str, methods: Iterable[str]
) -> PAT | None:
    """Replace the per-PAT HTTP method allowlist.

    Empty list clears the restriction (token usable with any verb).
    Raises ``ValueError`` on unknown verbs so the caller surfaces a
    structured 400; returns None when the token is unknown or owned
    by another tenant.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe = normalise_http_methods(list(methods))
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(safe),
        "usage_windows": sorted(current.usage_windows),
        "http_methods_updated_at": time.time(),
        "owner_email": current.owner_email,
        "description": current.description,
    }
    _append(rec)
    return _from_record(rec)


# --- Owner email -----------------------------------------------------

_OWNER_EMAIL_MAX = 128
_OWNER_EMAIL_RE = __import__("re").compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)


def normalise_owner_email(raw: str | None, *, allow_blank: bool = True) -> str:
    """Validate and lowercase a PAT owner email.

    Empty / None is accepted when ``allow_blank`` is True (the field is
    optional by default so existing tokens stay legal). Non-empty input
    is stripped, lowercased, length-capped, and matched against a
    pragmatic email regex. Raises :class:`ValueError` on bad input so
    callers surface a structured 400.
    """
    s = (raw or "").strip().lower()
    if not s:
        if allow_blank:
            return ""
        raise ValueError("owner_email is required")
    if len(s) > _OWNER_EMAIL_MAX:
        raise ValueError(f"owner_email must be <= {_OWNER_EMAIL_MAX} chars")
    if not _OWNER_EMAIL_RE.match(s):
        raise ValueError("owner_email must look like name@example.com")
    return s


def set_owner_email(
    *, tenant_id: str, pat_id: str, owner_email: str
) -> "PAT | None":
    """Replace the per-PAT owner email contact.

    Empty string clears the value. Returns None when the token is
    unknown or owned by another tenant so the caller surfaces a 404
    without leaking existence across tenants. Raises :class:`ValueError`
    when ``owner_email`` is non-blank and fails validation.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe_email = normalise_owner_email(owner_email, allow_blank=True)
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "owner_email": safe_email,
        "owner_email_updated_at": time.time(),
        "description": current.description,
    }
    _append(rec)
    return _from_record(rec)


# --- Description / purpose note --------------------------------------

_DESCRIPTION_MAX = 200


def normalise_description(raw: str | None) -> str:
    """Validate and canonicalise a PAT description / purpose note.

    Empty / None becomes empty string (the field is optional so legacy
    tokens stay legal). Non-empty input is stripped, has ASCII control
    characters removed (newlines collapsed to single spaces so the
    inventory UI renders one line cleanly), and length-capped to
    :data:`_DESCRIPTION_MAX`. Raises :class:`ValueError` on overlong
    input so callers surface a structured 400.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = "".join(ch if ch >= " " or ch == " " else " " for ch in s)
    s = " ".join(s.split())
    if len(s) > _DESCRIPTION_MAX:
        raise ValueError(
            f"description must be <= {_DESCRIPTION_MAX} chars"
        )
    return s


def set_description(
    *, tenant_id: str, pat_id: str, description: str
) -> "PAT | None":
    """Replace the per-PAT description / purpose note.

    Empty string clears the value. Returns None when the token is
    unknown or owned by another tenant so the caller surfaces a 404
    without leaking existence across tenants. Raises :class:`ValueError`
    when ``description`` exceeds the length cap.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe = normalise_description(description)
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(current.usage_windows),
        "owner_email": current.owner_email,
        "description": current.description,
        "description": safe,
        "description_updated_at": time.time(),
    }
    _append(rec)
    return _from_record(rec)


# --- Per-PAT usage windows ------------------------------------------

_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_days(spec: str) -> frozenset[int]:
    """Parse ``mon``, ``mon-fri``, or ``all`` into a set of weekday ints (Mon=0)."""
    s = spec.strip().lower()
    if not s:
        raise ValueError("empty day spec")
    if s == "all":
        return frozenset(range(7))
    if "-" in s:
        a, b = s.split("-", 1)
        if a not in _DAY_INDEX or b not in _DAY_INDEX:
            raise ValueError(f"unknown day in range: {spec!r}")
        i, j = _DAY_INDEX[a], _DAY_INDEX[b]
        if i <= j:
            return frozenset(range(i, j + 1))
        # Wrap, e.g. fri-mon means Fri, Sat, Sun, Mon.
        return frozenset(list(range(i, 7)) + list(range(0, j + 1)))
    if s not in _DAY_INDEX:
        raise ValueError(f"unknown day: {spec!r}")
    return frozenset({_DAY_INDEX[s]})


def _parse_hhmm(spec: str) -> int:
    """Return minutes since midnight, or raise ValueError."""
    if len(spec) != 5 or spec[2] != ":":
        raise ValueError(f"time must be HH:MM, got {spec!r}")
    try:
        h = int(spec[:2])
        m = int(spec[3:])
    except ValueError:
        raise ValueError(f"time must be HH:MM, got {spec!r}")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time out of range: {spec!r}")
    return h * 60 + m


def _parse_window(spec: str) -> tuple[frozenset[int], int, int]:
    """Parse a single ``<days>:HH:MM-HH:MM`` window. UTC.

    Returns (days_set, start_minute, end_minute). End is inclusive on
    the minute. If end < start the window is treated as wrapping past
    midnight by the matcher.
    """
    s = (spec or "").strip().lower()
    if not s:
        raise ValueError("empty usage window")
    # Split on first ':' between days and the time range.
    idx = s.find(":")
    if idx < 0 or idx == len(s) - 1:
        raise ValueError(
            f"usage window must be <days>:HH:MM-HH:MM, got {spec!r}"
        )
    days_part = s[:idx]
    time_part = s[idx + 1 :]
    if "-" not in time_part:
        raise ValueError(
            f"usage window time range must be HH:MM-HH:MM, got {spec!r}"
        )
    start_s, end_s = time_part.split("-", 1)
    days = _parse_days(days_part)
    start = _parse_hhmm(start_s)
    end = _parse_hhmm(end_s)
    return days, start, end


def normalise_usage_windows(raw: Iterable[str] | None) -> frozenset[str]:
    """Validate and canonicalise a list of usage-window specs.

    Each entry is lowercased, parsed, and re-rendered in the canonical
    ``<days>:HH:MM-HH:MM`` form (days expanded back into the shortest
    valid spec: ``all`` for all 7 days, a single day token when only
    one weekday is set, or the original dash range form when
    contiguous). Garbage strings raise :class:`ValueError`. Empty
    input is the empty frozenset which the enforcer treats as
    \"no restriction\".

    Hard cap: 16 windows per PAT. More than that is almost always a
    misconfiguration and we'd rather fail closed than carry an
    unbounded list around.
    """
    if not raw:
        return frozenset()
    items = list(raw)
    if len(items) > 16:
        raise ValueError("at most 16 usage windows per token")
    out: set[str] = set()
    for entry in items:
        days, start, end = _parse_window(str(entry))
        # Canonicalise the day token.
        if len(days) == 7:
            day_token = "all"
        elif len(days) == 1:
            (only,) = days
            day_token = _DAY_ORDER[only]
        else:
            # Try to render as a contiguous range (no wrap) for
            # readability; fall back to a comma-joined list (which the
            # parser does NOT accept, so split into one window per day
            # in that case to keep round-tripping honest).
            sorted_days = sorted(days)
            contiguous = all(
                sorted_days[i] + 1 == sorted_days[i + 1]
                for i in range(len(sorted_days) - 1)
            )
            if contiguous:
                day_token = f"{_DAY_ORDER[sorted_days[0]]}-{_DAY_ORDER[sorted_days[-1]]}"
            else:
                # Emit one window per day rather than invent a list
                # syntax the parser doesn't accept.
                for d in sorted_days:
                    out.add(
                        f"{_DAY_ORDER[d]}:{start // 60:02d}:{start % 60:02d}-"
                        f"{end // 60:02d}:{end % 60:02d}"
                    )
                continue
        canon = (
            f"{day_token}:{start // 60:02d}:{start % 60:02d}-"
            f"{end // 60:02d}:{end % 60:02d}"
        )
        out.add(canon)
    if len(out) > 16:
        raise ValueError("at most 16 usage windows per token")
    return frozenset(out)


def usage_window_matches(now_epoch: float, windows: Iterable[str]) -> bool:
    """Return True when ``now_epoch`` falls inside at least one window.

    Empty / missing windows means \"no restriction\" and is always
    True so existing PATs stay backward compatible. All comparisons
    are in UTC: usage windows are a server-side fence and a tenant
    that wants Pacific business hours can express them as the
    corresponding UTC range. Windows where end < start are treated as
    wrapping past midnight (e.g. ``all:22:00-02:00``); in that case
    the weekday gate matches if *either* the current UTC weekday or
    the previous UTC weekday is in the day set so a window spanning
    midnight covers both halves.
    """
    win_list = [w for w in windows if w]
    if not win_list:
        return True
    import time as _time
    t = _time.gmtime(now_epoch)
    # Python's tm_wday is already Mon=0 ... Sun=6, matching _DAY_INDEX.
    today = t.tm_wday
    yesterday = (today - 1) % 7
    minute_of_day = t.tm_hour * 60 + t.tm_min
    for w in win_list:
        try:
            days, start, end = _parse_window(str(w))
        except ValueError:
            # Treat unparseable persisted windows as deny rather than
            # accidentally opening the gate.
            continue
        if start <= end:
            # Non-wrapping: today must be in the day set and minute
            # must fall within [start, end] inclusive.
            if today in days and start <= minute_of_day <= end:
                return True
        else:
            # Wrapping past midnight.
            if today in days and minute_of_day >= start:
                return True
            if yesterday in days and minute_of_day <= end:
                return True
    return False


def set_usage_windows(
    *, tenant_id: str, pat_id: str, windows: Iterable[str]
) -> "PAT | None":
    """Replace the per-PAT usage window list.

    Empty list clears the restriction (token usable any time).
    Raises :class:`ValueError` on malformed specs so the route
    surfaces a structured 400; returns None when the token is
    unknown or owned by another tenant so the route can respond
    with 404 without leaking cross-tenant existence.
    """
    current = _reduce().get(pat_id)
    if current is None or current.deleted or current.tenant_id != tenant_id:
        return None
    safe = normalise_usage_windows(list(windows))
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
        "ip_cidrs": sorted(current.ip_cidrs),
        "path_prefixes": sorted(current.path_prefixes),
        "require_device_approval": current.require_device_approval,
        "http_methods": sorted(current.http_methods),
        "usage_windows": sorted(safe),
        "owner_email": current.owner_email,
        "description": current.description,
        "usage_windows_updated_at": time.time(),
    }
    _append(rec)
    return _from_record(rec)
