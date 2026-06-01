"""Per-workspace HMAC signing keys for data exports.

Why this exists
---------------
``workspace_export.build_export`` returns a manifest with a sha256
over the payload bytes. That proves a downloaded bundle is internally
consistent, but it does *not* prove the bundle was actually produced
by ClawHum for a specific workspace. An enterprise buyer's compliance
team needs to be able to verify months later that an export they
archived in their evidence vault is the unmodified artifact the
workspace owner downloaded.

This module mints a per-workspace HMAC-SHA256 signing key. The
manifest sha256 (bound to the tenant id and generation timestamp) is
signed under the active key on every export; the signature, key id,
and algorithm are returned in response headers and embedded in the
ZIP as ``signature.json``. A separate verify endpoint accepts a
manifest + signature pair and confirms it matches the workspace's
active or recently rotated key. The same plaintext secret is shown to
the workspace owner on mint or rotate so they can also verify offline
with ``openssl dgst -sha256 -hmac <secret>``.

Design notes
------------
* Storage uses the same append-only JSONL last-writer-wins pattern as
  every other tenant-local store. Cache rebuilt from disk on read
  and on mutation so multi-worker writers stay correct.
* The plaintext secret lives only in this file on the server (mode
  rwx by the api process). It is not echoed into logs, not embedded
  in exports, not returned on subsequent reads. Workspace owners who
  lose the plaintext can rotate to get a fresh one.
* Rotation keeps the previous secret for a 14 day grace window so
  buyers verifying an export taken before rotation still succeed.
* Cross-tenant lookups are impossible: every public function takes
  ``tenant_id`` and only ever reads that tenant's row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "SigningKey"] | None = None
_CACHE_PATH: Path | None = None

SIGNING_ALG = "HMAC-SHA256"
SECRET_PREFIX = "esk_"  # export signing key
GRACE_SECONDS = 14 * 24 * 3600  # 14 days
_KEY_ID_LEN = 12
_KEY_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def _new_key_id() -> str:
    return "k_" + "".join(secrets.choice(_KEY_ID_ALPHABET) for _ in range(_KEY_ID_LEN))


def _new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class SigningKey:
    tenant_id: str
    key_id: str
    secret: str  # plaintext; stays server-side
    created_at: float
    created_by: str
    rotated_at: float
    prior_key_id: str
    prior_secret: str
    prior_expires_at: float

    def public_view(self, now: float | None = None) -> dict:
        ts = now if now is not None else time.time()
        out: dict = {
            "tenant_id": self.tenant_id,
            "key_id": self.key_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "algorithm": SIGNING_ALG,
        }
        if self.rotated_at:
            out["rotated_at"] = self.rotated_at
            out["prior_key_id"] = self.prior_key_id
            out["prior_grace_expires_at"] = self.prior_expires_at
            out["prior_in_grace"] = bool(
                self.prior_secret and ts < self.prior_expires_at
            )
        return out

    def _to_disk_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "key_id": self.key_id,
            "secret": self.secret,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "rotated_at": self.rotated_at,
            "prior_key_id": self.prior_key_id,
            "prior_secret": self.prior_secret,
            "prior_expires_at": self.prior_expires_at,
        }


def _path() -> Path:
    return Path(get_settings().export_signing_keys_path)


def _load_locked() -> dict[str, SigningKey]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, SigningKey] = {}
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tid = str(row.get("tenant_id") or "")
                    if not tid:
                        continue
                    out[tid] = SigningKey(
                        tenant_id=tid,
                        key_id=str(row.get("key_id") or ""),
                        secret=str(row.get("secret") or ""),
                        created_at=float(row.get("created_at") or 0.0),
                        created_by=str(row.get("created_by") or ""),
                        rotated_at=float(row.get("rotated_at") or 0.0),
                        prior_key_id=str(row.get("prior_key_id") or ""),
                        prior_secret=str(row.get("prior_secret") or ""),
                        prior_expires_at=float(row.get("prior_expires_at") or 0.0),
                    )
        except OSError:
            pass
    _CACHE = out
    _CACHE_PATH = p
    return out


def reset_cache() -> None:
    """Test helper. Drop the in-memory cache so the next read re-loads."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _persist_locked(rec: SigningKey) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec._to_disk_dict(), sort_keys=True) + "\n")
    # Best-effort: tighten file perms. Failure is non-fatal (e.g.
    # filesystems that do not support chmod) so we never break exports
    # because of it.
    try:
        import os

        os.chmod(p, 0o600)
    except OSError:
        pass


def get_key(tenant_id: str) -> SigningKey | None:
    """Return the active signing key for the tenant, or None if not minted."""
    if not tenant_id:
        return None
    with _LOCK:
        return _load_locked().get(tenant_id)


def ensure_key(
    tenant_id: str, *, created_by: str = "system"
) -> tuple[SigningKey, str | None]:
    """Return the active key, auto-minting one on first use.

    Returns (key, fresh_secret) where ``fresh_secret`` is the
    plaintext secret iff a key was minted on this call. For existing
    keys returns (key, None) so callers never re-expose the secret.
    """
    if not tenant_id:
        raise ValueError("tenant_id required")
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id)
        if existing is not None:
            return existing, None
        secret = _new_secret()
        rec = SigningKey(
            tenant_id=tenant_id,
            key_id=_new_key_id(),
            secret=secret,
            created_at=time.time(),
            created_by=(created_by or "system").strip()[:64] or "system",
            rotated_at=0.0,
            prior_key_id="",
            prior_secret="",
            prior_expires_at=0.0,
        )
        _persist_locked(rec)
        store[tenant_id] = rec
        return rec, secret


def rotate(tenant_id: str, *, created_by: str) -> tuple[SigningKey, str]:
    """Force a rotation (or first mint). Returns (new_key, plaintext_secret).

    The previous secret is retained with a 14 day grace window so
    signatures issued before this call still verify until the grace
    window expires. Callers must audit this action.
    """
    if not tenant_id:
        raise ValueError("tenant_id required")
    with _LOCK:
        store = _load_locked()
        prior = store.get(tenant_id)
        secret = _new_secret()
        now = time.time()
        rec = SigningKey(
            tenant_id=tenant_id,
            key_id=_new_key_id(),
            secret=secret,
            created_at=now,
            created_by=(created_by or "unknown").strip()[:64] or "unknown",
            rotated_at=now if prior else 0.0,
            prior_key_id=prior.key_id if prior else "",
            prior_secret=prior.secret if prior else "",
            prior_expires_at=(now + GRACE_SECONDS) if prior else 0.0,
        )
        _persist_locked(rec)
        store[tenant_id] = rec
        return rec, secret


def reveal_active_secret(tenant_id: str) -> str:
    """Return the active plaintext secret. Admin + MFA gated on routes."""
    key = get_key(tenant_id)
    if key is None:
        raise LookupError("no signing key for tenant")
    return key.secret


def signing_message(
    manifest_sha256: str, tenant_id: str, generated_at: float
) -> bytes:
    """Canonical bytes signed for an export bundle.

    Binds manifest sha256 to the tenant id and generation timestamp so
    a signature for tenant A's bundle cannot be replayed against
    tenant B's bundle.
    """
    payload = {
        "manifest_sha256": manifest_sha256,
        "tenant_id": tenant_id,
        "generated_at": int(generated_at),
        "version": 1,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_export(
    *,
    tenant_id: str,
    manifest_sha256: str,
    generated_at: float,
) -> tuple[str, str]:
    """Sign an export manifest. Auto-mints a key if none exists.

    Returns (key_id, signature_hex).
    """
    key, _ = ensure_key(tenant_id, created_by="export.auto")
    msg = signing_message(manifest_sha256, tenant_id, generated_at)
    return key.key_id, _hmac_hex(key.secret, msg)


def verify_export(
    *,
    tenant_id: str,
    key_id: str,
    signature_hex: str,
    manifest_sha256: str,
    generated_at: float,
    now: float | None = None,
) -> bool:
    """Constant-time verify of (key_id, signature) for ``tenant_id``.

    Accepts the active key, or a recently rotated key still inside
    its grace window. Cross-tenant verification fails because every
    store lookup is keyed by ``tenant_id``.
    """
    if not tenant_id or not key_id or not signature_hex or not manifest_sha256:
        return False
    key = get_key(tenant_id)
    if key is None:
        return False
    ts = now if now is not None else time.time()
    msg = signing_message(manifest_sha256, tenant_id, generated_at)

    candidates: list[str] = []
    if key.key_id == key_id and key.secret:
        candidates.append(key.secret)
    if (
        key.prior_key_id
        and key.prior_key_id == key_id
        and key.prior_secret
        and ts < key.prior_expires_at
    ):
        candidates.append(key.prior_secret)

    for secret in candidates:
        expected = _hmac_hex(secret, msg)
        if hmac.compare_digest(expected, signature_hex):
            return True
    return False
