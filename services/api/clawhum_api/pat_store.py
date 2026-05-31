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

from .api_keys import ROLES

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
    for tok in _reduce().values():
        if tok.deleted:
            continue
        if hmac_compare(tok.secret_hash, h):
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


def create(
    *,
    tenant_id: str,
    name: str,
    roles: frozenset[str],
    rpm: int = 0,
) -> tuple[PAT, str]:
    """Mint a new PAT. Returns (record, plaintext_secret_shown_once)."""
    safe_roles = frozenset(r for r in roles if r in ROLES) or frozenset({"reader"})
    secret = new_secret()
    rec = {
        "id": _new_id(),
        "tenant_id": tenant_id,
        "name": (name or "").strip()[:64] or "untitled",
        "roles": sorted(safe_roles),
        "rpm": max(0, int(rpm or 0)),
        "created_at": time.time(),
        "last_used_at": 0.0,
        "secret_hash": hash_secret(secret),
        "secret_hint": secret[-4:],
        "deleted": False,
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
        "revoked_at": time.time(),
    }
    _append(rec)
    return True


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
    }
