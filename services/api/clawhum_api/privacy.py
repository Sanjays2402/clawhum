"""GDPR data lifecycle helpers.

Provides export and delete operations scoped to the caller's actor id.
The actor id is the same hashed identifier the audit middleware writes,
so a caller can request exactly the audit trail attributable to their
API key without leaking other tenants' rows.

Delete is implemented as redaction: the audit log is append only and
must retain immutable forensic shape (timestamp, method, path, status),
but personally identifying fields (actor digest, client_ip, user_agent,
request_id) are replaced with the literal string "redacted". This
satisfies the GDPR right to erasure while preserving the integrity of
the security log.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

# Single lock for read-modify-write on the audit file. Audit writes
# take a separate lock in audit.py; both must be held in the same
# order to avoid deadlock, so privacy operations acquire only this
# lock and rely on POSIX rename atomicity for the swap.
_redact_lock = threading.Lock()

_REDACTED = "redacted"
_REDACT_FIELDS = ("actor", "client_ip", "user_agent", "request_id")


def actor_id_for(api_key: str | None) -> str:
    """Return the same stable digest used by the audit middleware."""
    if not api_key:
        return "anonymous"
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"key:{digest[:16]}"


def _iter_events(path: Path):
    if not path.exists():
        return
    with open(path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Skip corrupt lines rather than crash the export.
                continue


def collect_events(actor: str, path: Path) -> list[dict[str, Any]]:
    """Return every audit event attributed to the given actor."""
    return [ev for ev in _iter_events(path) if ev.get("actor") == actor]


def redact_actor(actor: str, path: Path) -> int:
    """Redact actor-identifying fields for every event matching actor.

    Returns the number of events that were modified. The file is
    rewritten atomically via tempfile + os.replace so a crash mid
    redaction leaves either the original or the fully redacted file,
    never a half written one.
    """
    if not path.exists():
        return 0

    redacted_count = 0
    with _redact_lock:
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(fd, "wb") as out:
                for ev in _iter_events(path):
                    if ev.get("actor") == actor:
                        for field in _REDACT_FIELDS:
                            if field in ev:
                                ev[field] = _REDACTED
                        redacted_count += 1
                    line = json.dumps(ev, separators=(",", ":"), sort_keys=True)
                    out.write(line.encode("utf-8") + b"\n")
            os.replace(tmp_name, path)
        except Exception:
            # Clean up the half written temp file on any error.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise

    return redacted_count


_FEEDBACK_REDACT_FIELDS = ("query_id", "track_id")


def redact_tenant_feedback(tenant_id: str, path: str | Path) -> int:
    """Redact feedback identifiers for every row matching tenant_id.

    Mirrors redact_actor: rewrites the JSONL file atomically via
    tempfile + os.replace. Preserves row shape and the tenant_id tag so
    downstream aggregate analytics still see the row as belonging to
    the tenant; only the identifying fields query_id and track_id are
    replaced with the literal string "redacted". Returns the count of
    rows that were modified.
    """
    fpath = Path(path)
    if not fpath.exists():
        return 0

    redacted = 0
    with _redact_lock:
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=fpath.name + ".", suffix=".tmp", dir=str(fpath.parent)
        )
        try:
            with os.fdopen(fd, "wb") as out:
                for ev in _iter_events(fpath):
                    if ev.get("tenant_id") == tenant_id:
                        for field in _FEEDBACK_REDACT_FIELDS:
                            if field in ev:
                                ev[field] = _REDACTED
                        redacted += 1
                    line = json.dumps(ev, separators=(",", ":"), sort_keys=True)
                    out.write(line.encode("utf-8") + b"\n")
            os.replace(tmp_name, fpath)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise

    return redacted
