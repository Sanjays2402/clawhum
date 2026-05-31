"""Time-based one-time password (TOTP) MFA for admin actions.

This module exists because enterprise procurement reviews routinely
block deals over the lack of step-up authentication on destructive
admin endpoints. API keys alone protect against drive-by abuse but
not against a single leaked token taking down a tenant: revoking
sibling keys, wiping audit redactable fields, mutating IP allowlists,
deleting webhooks. TOTP gates exactly those actions with a second
factor the attacker must also have stolen.

Design notes:

* Enrollment is scoped to ``actor_id`` (the same hashed identifier
  the audit middleware writes), so it sticks to whoever holds the
  bearer token, not to the tenant. Every distinct API key or PAT
  enrolls independently, which matches how human operators rotate
  credentials.
* The shared secret is generated server side, 20 random bytes encoded
  as base32 per RFC 4226. We hand the user both the raw base32 and an
  ``otpauth://`` URI they can paste into any authenticator.
* Enrollment is two phase: the secret is pending until the user
  proves possession of an authenticator generating valid codes. Only
  then does the record flip to ``verified=True`` and the gate engages.
* Recovery codes (10 single-use strings) are minted at verification
  time, shown exactly once, and stored hashed so a leaked store file
  cannot be replayed. Spent codes are tombstoned.
* The store is the same append-only JSONL pattern the rest of the
  codebase uses (PATs, webhooks, share), so it inherits the same
  multi-worker semantics and the same backup story.

The implementation deliberately depends only on the Python stdlib so
the deal-blocker fix does not pull a new transitive dependency into a
codebase that is already audited for supply-chain risk.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from clawhum_core.settings import get_settings


_LOCK = Lock()
_DEFAULT_STEP = 30  # seconds, RFC 6238 standard.
_DEFAULT_DIGITS = 6
_DEFAULT_WINDOW = 1  # accept the previous and next step too (clock skew).
_RECOVERY_COUNT = 10
_RECOVERY_BYTES = 5  # 10 chars of base32 per code.


def _b32_secret(num_bytes: int = 20) -> str:
    """Return a fresh base32 TOTP secret with no padding (authenticator
    apps reject `=` padding inconsistently). 20 raw bytes is what the
    RFC 4226 reference and Google Authenticator use."""
    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _b32_decode(secret: str) -> bytes:
    pad = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret.upper() + pad)


def totp(secret_b32: str, *, t: float | None = None, step: int = _DEFAULT_STEP,
         digits: int = _DEFAULT_DIGITS) -> str:
    """Compute the current TOTP value for ``secret_b32``.

    Implements RFC 6238 with HMAC-SHA1, the default every authenticator
    app speaks. Kept tiny on purpose so the security review can read it
    top to bottom: no third-party crypto, no surprises.
    """
    key = _b32_decode(secret_b32)
    counter = int((t if t is not None else time.time()) // step)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (
        ((h[offset] & 0x7F) << 24)
        | ((h[offset + 1] & 0xFF) << 16)
        | ((h[offset + 2] & 0xFF) << 8)
        | (h[offset + 3] & 0xFF)
    )
    return str(code % (10 ** digits)).zfill(digits)


def verify_code(secret_b32: str, code: str, *, t: float | None = None,
                step: int = _DEFAULT_STEP, window: int = _DEFAULT_WINDOW,
                digits: int = _DEFAULT_DIGITS) -> bool:
    """Constant time TOTP verification with a small clock-skew window.

    A window of 1 means we accept codes one step before and one step
    after the current step, which is what RFC 6238 section 5.2
    recommends for handling end-user device drift while keeping the
    brute force area tiny (3 * 10^6 codes vs the rate limit makes
    guessing infeasible).
    """
    code = (code or "").strip()
    if len(code) != digits or not code.isdigit():
        return False
    now = t if t is not None else time.time()
    for offset in range(-window, window + 1):
        candidate = totp(secret_b32, t=now + offset * step, step=step, digits=digits)
        if hmac.compare_digest(candidate, code):
            return True
    return False


def provisioning_uri(secret_b32: str, *, account: str, issuer: str = "Clawhum") -> str:
    """Build the standard ``otpauth://totp/`` URI authenticator apps consume.

    Authenticator apps render this as a QR code when shown as one, and
    accept it pasted into the manual entry flow."""
    label = urllib.parse.quote(f"{issuer}:{account}", safe="")
    params = urllib.parse.urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": _DEFAULT_DIGITS,
            "period": _DEFAULT_STEP,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_recovery_codes(n: int = _RECOVERY_COUNT) -> list[str]:
    """Mint user-readable recovery codes formatted like ``ABCDE-FGHIJ``.

    Base32 keeps them unambiguous (no 0/O, 1/I confusion) and short
    enough to read off a printed sheet during incident response.
    """
    out: list[str] = []
    for _ in range(n):
        raw = secrets.token_bytes(_RECOVERY_BYTES)
        s = base64.b32encode(raw).decode("ascii").rstrip("=")
        out.append(f"{s[:5]}-{s[5:]}")
    return out


@dataclass(frozen=True)
class MfaRecord:
    actor_id: str
    tenant_id: str
    secret: str
    verified: bool
    created_at: float
    verified_at: float | None
    recovery_hashes: tuple[str, ...]
    spent_recovery: tuple[str, ...]


def _path() -> Path:
    p = get_settings().mfa_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _iter(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return ()
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _index() -> dict[str, MfaRecord]:
    """Replay the JSONL log into the latest-record-wins map.

    A line with ``deleted=True`` tombstones an actor's enrollment so a
    user can disable MFA and start over.
    """
    rows: dict[str, MfaRecord] = {}
    for rec in _iter(_path()):
        actor = rec.get("actor_id")
        if not actor:
            continue
        if rec.get("deleted"):
            rows.pop(actor, None)
            continue
        rows[actor] = MfaRecord(
            actor_id=actor,
            tenant_id=str(rec.get("tenant_id") or ""),
            secret=str(rec.get("secret") or ""),
            verified=bool(rec.get("verified", False)),
            created_at=float(rec.get("created_at") or 0),
            verified_at=(float(rec["verified_at"]) if rec.get("verified_at") else None),
            recovery_hashes=tuple(rec.get("recovery_hashes") or ()),
            spent_recovery=tuple(rec.get("spent_recovery") or ()),
        )
    return rows


def _append(rec: dict[str, Any]) -> None:
    path = _path()
    line = json.dumps(rec, separators=(",", ":"), sort_keys=True)
    with _LOCK:
        with open(path, "ab") as f:
            f.write(line.encode("utf-8") + b"\n")


def actor_id_for(api_key: str | None) -> str:
    """Match audit.py and privacy.py so MFA lives next to the audit row."""
    if not api_key:
        return "anonymous"
    return f"key:{hashlib.sha256(api_key.encode('utf-8')).hexdigest()[:16]}"


def get(actor_id: str) -> MfaRecord | None:
    return _index().get(actor_id)


def is_required(actor_id: str) -> bool:
    """A verified record means the gate is engaged for this actor."""
    rec = get(actor_id)
    return rec is not None and rec.verified


def begin_enrollment(actor_id: str, tenant_id: str, account_label: str) -> dict[str, Any]:
    """Mint a fresh pending secret. Overwrites any prior pending row.

    We do not mint recovery codes here; that happens at verification so
    a user who walks away from the enroll page does not leak codes that
    were never paired to a working authenticator.
    """
    existing = get(actor_id)
    if existing is not None and existing.verified:
        return {"already_verified": True}
    secret = _b32_secret()
    now = time.time()
    _append(
        {
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "secret": secret,
            "verified": False,
            "created_at": now,
            "verified_at": None,
            "recovery_hashes": [],
            "spent_recovery": [],
        }
    )
    return {
        "secret": secret,
        "otpauth": provisioning_uri(secret, account=account_label),
        "digits": _DEFAULT_DIGITS,
        "period": _DEFAULT_STEP,
    }


def complete_enrollment(actor_id: str, code: str) -> dict[str, Any]:
    """Verify the user's first code, flip ``verified=True`` and mint
    one-shot recovery codes. Returns the recovery codes in plaintext
    exactly once; only their hashes are persisted."""
    rec = get(actor_id)
    if rec is None:
        return {"ok": False, "error": "no_pending_enrollment"}
    if rec.verified:
        return {"ok": False, "error": "already_verified"}
    if not verify_code(rec.secret, code):
        return {"ok": False, "error": "invalid_code"}
    recovery = _new_recovery_codes()
    now = time.time()
    _append(
        {
            "actor_id": actor_id,
            "tenant_id": rec.tenant_id,
            "secret": rec.secret,
            "verified": True,
            "created_at": rec.created_at,
            "verified_at": now,
            "recovery_hashes": [_hash(c) for c in recovery],
            "spent_recovery": list(rec.spent_recovery),
        }
    )
    return {"ok": True, "recovery_codes": recovery}


def disable(actor_id: str, code: str) -> dict[str, Any]:
    """Disable MFA. Requires a current TOTP or recovery code so a
    stolen API key alone cannot turn the second factor off."""
    rec = get(actor_id)
    if rec is None:
        return {"ok": False, "error": "not_enrolled"}
    if not rec.verified:
        _append({"actor_id": actor_id, "deleted": True})
        return {"ok": True}
    if not _consume(rec, code):
        return {"ok": False, "error": "invalid_code"}
    _append({"actor_id": actor_id, "deleted": True})
    return {"ok": True}


def _consume(rec: MfaRecord, code: str) -> bool:
    """Accept a live TOTP or burn a recovery code. Recovery is one-shot.

    On a successful recovery burn we append the updated record so the
    next replay of the log does not let the same code work twice.
    """
    code = (code or "").strip()
    if not code:
        return False
    if verify_code(rec.secret, code):
        return True
    norm = code.upper().replace(" ", "")
    h = _hash(norm)
    if h in rec.recovery_hashes:
        remaining = tuple(c for c in rec.recovery_hashes if c != h)
        spent = rec.spent_recovery + (h,)
        _append(
            {
                "actor_id": rec.actor_id,
                "tenant_id": rec.tenant_id,
                "secret": rec.secret,
                "verified": True,
                "created_at": rec.created_at,
                "verified_at": rec.verified_at,
                "recovery_hashes": list(remaining),
                "spent_recovery": list(spent),
            }
        )
        return True
    return False


def verify(actor_id: str, code: str) -> bool:
    """Public verification used by the require_mfa() dependency."""
    rec = get(actor_id)
    if rec is None or not rec.verified:
        return True
    return _consume(rec, code)


def status(actor_id: str) -> dict[str, Any]:
    rec = get(actor_id)
    if rec is None:
        return {"enrolled": False, "verified": False, "recovery_remaining": 0}
    return {
        "enrolled": True,
        "verified": rec.verified,
        "recovery_remaining": len(rec.recovery_hashes),
        "created_at": rec.created_at,
        "verified_at": rec.verified_at,
    }
