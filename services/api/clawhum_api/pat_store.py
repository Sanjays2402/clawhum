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
import json
import secrets
import time
from dataclasses import dataclass
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
    deleted: bool = False
    expires_at: float = 0.0  # 0.0 means "never expires"
    # Fine-grained scopes layered on top of roles. An empty set means
    # "every scope this PAT's roles permit", which keeps PATs minted
    # before this field existed working without a migration.
    scopes: frozenset[str] = frozenset()

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
        deleted=bool(rec.get("deleted", False)),
        expires_at=float(rec.get("expires_at", 0.0) or 0.0),
        scopes=normalise_scopes(rec.get("scopes") or []),
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
    secret = new_secret()
    now = time.time()
    expires_at = resolve_expiry(requested_days=expires_in_days, now=now)
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
        "deleted": False,
        "expires_at": expires_at,
        "scopes": sorted(safe_scopes),
    }
    _append(rec)
    return _from_record(rec), secret


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
        "deleted": True,
        "expires_at": current.expires_at,
        "scopes": sorted(current.scopes),
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


def touch_last_used(pat_id: str) -> None:
    """Append a no-op record that bumps last_used_at. Best-effort."""
    current = _reduce().get(pat_id)
    if current is None or current.deleted:
        return
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
        "deleted": False,
        "expires_at": current.expires_at,
        "scopes": sorted(current.scopes),
    }
    _append(rec)


def public_view(p: PAT) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "roles": sorted(p.roles),
        "rpm": p.rpm,
        "created_at": p.created_at,
        "last_used_at": p.last_used_at,
        "secret_hint": p.secret_hint,
        "expires_at": p.expires_at,
        "expired": p.is_expired(),
        "scopes": sorted(p.scopes),
        "effective_scopes": sorted(p.effective_scopes()),
    }
