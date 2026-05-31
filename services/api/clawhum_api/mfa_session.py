"""MFA step-up "sudo mode" sessions.

The repo already enforces TOTP on every destructive admin endpoint via
``require_mfa()``. That stops a leaked API key cold, but it produces a
brutal UX: an admin paging through the dashboard has to retype a six
digit code for every click that touches a sensitive route. Real
enterprise consoles (GitHub sudo mode, AWS, Stripe reauth) issue a
short lived, server signed token after one successful MFA challenge
and accept it on subsequent destructive calls until it expires.

This module implements exactly that, with the constraints the rest of
the codebase enforces:

* Tokens are HMAC-SHA256 signed by a machine-local secret that lives
  in the same data directory as every other JSONL store. The secret
  is created on first use and reused across processes / workers.
* The signed payload binds the token to ``actor_id``, the issuing
  ``tenant_id``, the issue time, and an explicit expiry. A token
  presented by a different actor or tenant is rejected before any
  store lookup, so cross-actor replay is impossible even if a token
  leaks into a log.
* TTL is bounded by the per-workspace ``mfa_session_ttl_seconds``
  setting, hard-capped server side at ``mfa_session_max_ttl_seconds``.
  A workspace admin who wants strict reauth can dial the TTL down to
  zero to disable sudo mode entirely.
* A monotonically increasing ``revocation_epoch`` is persisted per
  ``(tenant_id, actor_id)``. Bumping the epoch invalidates every token
  ever issued to that actor in O(1) without re-issuing or scanning a
  store, which is what powers ``revoke_all()`` for force-logout.
* Disabling MFA or force-logging-out sessions bumps the epoch so a
  stolen token can never outlive the credential it was minted from.

The token format is ``v1.<base64url(payload)>.<base64url(sig)>`` where
``payload`` is a compact JSON object. Compact, URL-safe, stdlib only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings


_LOCK = Lock()
_SECRET_FILENAME = "mfa_session_signing.key"
_EPOCH_FILENAME = "mfa_session_epoch.json"
_VERSION = "v1"
_HARD_CAP_SECONDS = 60 * 60  # 1h absolute server-side ceiling.


def _data_dir() -> Path:
    # Co-locate with the other tenant stores. They all live under the
    # same ./data root so a single backup captures everything.
    return Path(get_settings().mfa_path).resolve().parent


def _secret_path() -> Path:
    return _data_dir() / _SECRET_FILENAME


def _epoch_path() -> Path:
    return _data_dir() / _EPOCH_FILENAME


def _load_or_create_secret() -> bytes:
    """Read the machine-local HMAC secret, creating one on first use.

    32 random bytes is the SHA-256 block size and matches every other
    HMAC use in this service. File mode is 0600 so a misconfigured
    backup does not expose it.
    """
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes().strip()
        if len(data) >= 32:
            return data
    with _LOCK:
        if path.exists():
            data = path.read_bytes().strip()
            if len(data) >= 32:
                return data
        raw = secrets.token_bytes(32)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return raw


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: bytes) -> bytes:
    return hmac.new(_load_or_create_secret(), payload, hashlib.sha256).digest()


# ---- epoch store ------------------------------------------------------

def _load_epochs() -> dict[str, int]:
    path = _epoch_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def _save_epochs(data: dict[str, int]) -> None:
    path = _epoch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True))
    tmp.replace(path)


def _epoch_key(tenant_id: str, actor_id: str) -> str:
    return f"{tenant_id}|{actor_id}"


def current_epoch(tenant_id: str, actor_id: str) -> int:
    with _LOCK:
        return int(_load_epochs().get(_epoch_key(tenant_id, actor_id), 0))


def bump_epoch(tenant_id: str, actor_id: str) -> int:
    """Invalidate every token previously issued for this actor.

    Returns the new epoch.
    """
    with _LOCK:
        data = _load_epochs()
        key = _epoch_key(tenant_id, actor_id)
        nxt = int(data.get(key, 0)) + 1
        data[key] = nxt
        _save_epochs(data)
        return nxt


def effective_ttl_seconds(tenant_id: str) -> int:
    """Per-tenant TTL, bounded by the global cap. 0 disables sudo mode."""
    settings = get_settings()
    requested = int(getattr(settings, "mfa_session_ttl_seconds", 300) or 0)
    cap = int(getattr(settings, "mfa_session_max_ttl_seconds", _HARD_CAP_SECONDS) or _HARD_CAP_SECONDS)
    cap = min(cap, _HARD_CAP_SECONDS)
    if requested <= 0:
        return 0
    return max(1, min(requested, cap))


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: int
    ttl_seconds: int
    epoch: int


def issue(*, tenant_id: str, actor_id: str, ttl_seconds: int | None = None,
          now: float | None = None) -> IssuedToken:
    """Issue a step-up session token bound to ``(tenant_id, actor_id)``."""
    if not tenant_id or not actor_id:
        raise ValueError("tenant_id and actor_id are required")
    settings_ttl = effective_ttl_seconds(tenant_id)
    if ttl_seconds is None:
        ttl = settings_ttl
    else:
        ttl = max(0, min(int(ttl_seconds), settings_ttl))
    if ttl <= 0:
        raise ValueError("mfa session disabled for tenant (ttl=0)")
    t0 = int(now if now is not None else time.time())
    epoch = current_epoch(tenant_id, actor_id)
    body = {
        "v": _VERSION,
        "t": tenant_id,
        "a": actor_id,
        "iat": t0,
        "exp": t0 + ttl,
        "e": epoch,
        "n": _b64u(secrets.token_bytes(8)),
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = _sign(payload)
    token = f"{_VERSION}.{_b64u(payload)}.{_b64u(sig)}"
    return IssuedToken(token=token, expires_at=body["exp"], ttl_seconds=ttl, epoch=epoch)


@dataclass(frozen=True)
class VerifyResult:
    valid: bool
    reason: str = ""
    expires_at: int = 0
    epoch: int = 0


def verify(token: str, *, tenant_id: str, actor_id: str,
           now: float | None = None) -> VerifyResult:
    """Verify a step-up token presented for ``(tenant_id, actor_id)``.

    Rejects on any of: malformed encoding, bad signature, version
    mismatch, wrong tenant, wrong actor, expired, or stale epoch.
    """
    if not token or not tenant_id or not actor_id:
        return VerifyResult(False, "missing")
    try:
        version, payload_b64, sig_b64 = token.split(".", 2)
    except ValueError:
        return VerifyResult(False, "malformed")
    if version != _VERSION:
        return VerifyResult(False, "version")
    try:
        payload = _b64u_decode(payload_b64)
        sig = _b64u_decode(sig_b64)
    except (ValueError, binascii.Error):
        return VerifyResult(False, "encoding")
    expected = _sign(payload)
    if not hmac.compare_digest(expected, sig):
        return VerifyResult(False, "signature")
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return VerifyResult(False, "payload")
    if body.get("t") != tenant_id:
        return VerifyResult(False, "tenant")
    if body.get("a") != actor_id:
        return VerifyResult(False, "actor")
    exp = int(body.get("exp", 0))
    t = int(now if now is not None else time.time())
    if exp <= t:
        return VerifyResult(False, "expired", expires_at=exp)
    if int(body.get("e", 0)) != current_epoch(tenant_id, actor_id):
        return VerifyResult(False, "revoked", expires_at=exp)
    return VerifyResult(True, "", expires_at=exp, epoch=int(body.get("e", 0)))


def revoke_all(tenant_id: str, actor_id: str) -> int:
    """Public alias for ``bump_epoch`` used by force-logout flows."""
    return bump_epoch(tenant_id, actor_id)


def status(tenant_id: str, actor_id: str) -> dict[str, Any]:
    ttl = effective_ttl_seconds(tenant_id)
    return {
        "enabled": ttl > 0,
        "ttl_seconds": ttl,
        "max_ttl_seconds": min(int(getattr(get_settings(), "mfa_session_max_ttl_seconds", _HARD_CAP_SECONDS)), _HARD_CAP_SECONDS),
        "epoch": current_epoch(tenant_id, actor_id),
    }
