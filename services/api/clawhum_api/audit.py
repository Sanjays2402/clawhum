"""Append-only audit log for mutating API actions.

Records who did what when, persisted to a JSONL file. Designed for
forensic review and compliance, not high-volume analytics.

In process size-based rotation is built in: when the active file
exceeds CLAWHUM_AUDIT_MAX_BYTES, it is renamed with a numeric suffix
(audit.jsonl.1, audit.jsonl.2, ...) and a fresh file is started.
CLAWHUM_AUDIT_BACKUP_COUNT bounds how many rotated files are kept;
older files are deleted. Set audit_max_bytes to 0 to opt out and use
external rotation (logrotate, sidecar) instead.

Captured for every non-GET, non-HEAD, non-OPTIONS request that reaches
the audit middleware. Read endpoints are skipped to keep the log
focused on state changes. Health and metrics paths are always skipped.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from clawhum_core.logging import get_logger
from clawhum_core.settings import get_settings
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_SKIP_PATHS = {"/health", "/ready", "/metrics"}
_SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}

# Genesis hash used as ``prev_hash`` for the very first entry in a
# fresh audit file. Picking a fixed, well-known constant lets a
# verifier distinguish "file starts at the beginning of history" from
# "someone truncated the head of the file" (the latter would leave the
# first surviving entry pointing at a hash no preceding line produced).
AUDIT_GENESIS_HASH = "0" * 64

_lock = threading.Lock()
_log = get_logger("clawhum.audit")

# Cache of the last entry hash per file path so consecutive writes can
# extend the hash chain without re-reading the file from disk. Reset
# on rotation and on process boot (when the cache is naturally empty).
_last_hash_by_path: dict[str, str] = {}


def hash_entry(prev_hash: str, body: dict[str, Any]) -> str:
    """Return the sha256 hex digest of ``prev_hash || canonical(body)``.

    ``body`` is serialised with ``sort_keys=True`` and the most compact
    separators so verifiers can recompute the same digest from a stored
    JSONL line by removing the existing ``entry_hash`` field and
    re-serialising the remainder. The digest is keyed by ``prev_hash``
    (genesis or a prior entry) so any reordering, deletion, or edit
    breaks the chain at the affected entry and every entry after it.
    """
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True)
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(b"|")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _read_last_hash(path: Path) -> str:
    """Return the ``entry_hash`` of the last well-formed line in ``path``.

    Used to seed the in-memory cache on first write after process boot
    so the chain continues from where the previous run left off. If the
    file is missing, empty, or the trailing line is malformed (which
    can only happen if an operator hand-edited it) the genesis hash is
    returned and the next entry starts a fresh chain segment.
    """
    try:
        with open(path, "rb") as f:
            tail = f.read()
    except FileNotFoundError:
        return AUDIT_GENESIS_HASH
    if not tail:
        return AUDIT_GENESIS_HASH
    # Walk from the end to find the last non-empty line. The file is
    # bounded by audit_max_bytes so reading it whole on boot is fine.
    for raw in reversed(tail.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            digest = row.get("entry_hash")
            if isinstance(digest, str) and len(digest) == 64:
                return digest
        except (json.JSONDecodeError, AttributeError):
            continue
        break
    return AUDIT_GENESIS_HASH


def _actor_id(api_key: str | None) -> str:
    """Return a stable, non-reversible id for an API key (or 'anonymous')."""
    if not api_key:
        return "anonymous"
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"key:{digest[:16]}"


def rotate_if_needed(
    path: Path,
    max_bytes: int,
    backup_count: int,
) -> bool:
    """Rotate the audit file if it exceeds max_bytes.

    Renames active -> .1, shifts existing .N -> .N+1, and deletes any
    backups beyond backup_count. Returns True if rotation happened.
    Safe to call with the audit write lock held; performs no IO when
    rotation is disabled (max_bytes <= 0) or the file is small enough.
    """
    if max_bytes <= 0 or backup_count <= 0:
        return False
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return False
    if size < max_bytes:
        return False

    # Drop the oldest backup if present.
    oldest = path.with_name(f"{path.name}.{backup_count}")
    with contextlib.suppress(FileNotFoundError):
        oldest.unlink()
    # Shift .N -> .N+1 for N in [backup_count-1 .. 1].
    for n in range(backup_count - 1, 0, -1):
        src = path.with_name(f"{path.name}.{n}")
        dst = path.with_name(f"{path.name}.{n + 1}")
        if src.exists():
            os.replace(src, dst)
    # Active -> .1.
    os.replace(path, path.with_name(f"{path.name}.1"))
    _log.info(
        "audit_rotated",
        path=str(path),
        rotated_bytes=size,
        backup_count=backup_count,
    )
    return True


def write_event(event: dict[str, Any], path: Path | None = None) -> None:
    """Append a single audit event as a JSON line. Best-effort, never raises.

    The persisted record is the caller-provided ``event`` plus two
    integrity fields: ``prev_hash`` (the entry hash of the previous
    line, or the genesis hash for a fresh file) and ``entry_hash``
    (sha256 of ``prev_hash || canonical(event_with_prev_hash)``). A
    verifier walks the file, recomputes each digest, and confirms the
    chain is intact. Tampering with any earlier line breaks every
    subsequent hash so deletes and edits are detectable after the fact.
    """
    settings = get_settings()
    target = path or settings.audit_log_path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            rotated = rotate_if_needed(
                target,
                max_bytes=settings.audit_max_bytes,
                backup_count=settings.audit_backup_count,
            )
            key = str(target.resolve()) if target.exists() or rotated else str(target)
            if rotated or key not in _last_hash_by_path:
                # Rotation produced a fresh empty file so the next
                # entry seeds a new chain segment starting at genesis.
                # On cache miss after boot, recover the prior tail.
                _last_hash_by_path[key] = AUDIT_GENESIS_HASH if rotated else _read_last_hash(target)
            prev_hash = _last_hash_by_path[key]
            body = dict(event)
            body["prev_hash"] = prev_hash
            digest = hash_entry(prev_hash, body)
            body["entry_hash"] = digest
            line = json.dumps(body, separators=(",", ":"), sort_keys=True)
            with open(target, "ab") as f:
                f.write(line.encode("utf-8") + b"\n")
            _last_hash_by_path[key] = digest
        # Fan out to the per-workspace SIEM forwarder (if configured).
        # Lives outside the file lock to keep the audit critical
        # section short; the forwarder itself never raises.
        try:
            from . import audit_forwarder

            audit_forwarder.enqueue_event(body)
        except Exception:  # pragma: no cover - defensive
            pass
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("audit_write_failed", error=str(exc))


def _reset_chain_cache() -> None:
    """Drop the cached last-hash table. Tests call this between runs."""
    with _lock:
        _last_hash_by_path.clear()


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Capture mutating requests and persist them to the audit log."""

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.method in _SKIP_METHODS or request.url.path in _SKIP_PATHS:
            return await call_next(request)

        started = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            try:
                actor = _actor_id(request.headers.get("x-api-key"))
                roles = getattr(request.state, "api_key_roles", None)
                event = {
                    "ts": started,
                    "actor": actor,
                    "api_key_name": getattr(request.state, "api_key_name", None),
                    "pat_id": getattr(request.state, "pat_id", None),
                    "session_id": getattr(request.state, "session_id", None),
                    "tenant_id": getattr(request.state, "tenant_id", None),
                    "roles": sorted(roles) if roles else [],
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "request_id": getattr(request.state, "request_id", None),
                    "trace_id": getattr(request.state, "trace_id", None),
                    "client_ip": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                    "duration_ms": round((time.time() - started) * 1000, 2),
                    "dry_run": bool(getattr(request.state, "dry_run", False)),
                }
                # Honor a test override path if the app set one.
                override = os.environ.get("CLAWHUM_AUDIT_LOG_PATH")
                path = Path(override) if override else None
                write_event(event, path=path)
                _log.info("audit", **event)
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning("audit_emit_failed", error=str(exc))
