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


def _rotated_paths(path: Path) -> list[Path]:
    """Return active path plus any rotated siblings, newest first."""
    paths: list[Path] = []
    if path.exists():
        paths.append(path)
    # Rotated files are named <name>.1, <name>.2, ... Walk until a gap.
    n = 1
    while True:
        candidate = path.with_name(f"{path.name}.{n}")
        if not candidate.exists():
            break
        paths.append(candidate)
        n += 1
    return paths


def _iter_events(path: Path):
    for fpath in _rotated_paths(path):
        with open(fpath, "rb") as f:
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


def _redact_file(
    fpath: Path,
    match_field: str,
    match_value: str,
    redact_fields: tuple[str, ...],
) -> int:
    """Rewrite fpath, redacting fields on rows where match_field == match_value.

    Atomic via tempfile + os.replace. Returns the number of rows modified.
    Skips silently if fpath does not exist.
    """
    if not fpath.exists():
        return 0
    redacted = 0
    directory = fpath.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=fpath.name + ".", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "wb") as out, open(fpath, "rb") as f:
            for raw in f:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get(match_field) == match_value:
                    for field in redact_fields:
                        if field in ev:
                            ev[field] = _REDACTED
                    redacted += 1
                out_line = json.dumps(ev, separators=(",", ":"), sort_keys=True)
                out.write(out_line.encode("utf-8") + b"\n")
        os.replace(tmp_name, fpath)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    return redacted


def redact_actor(actor: str, path: Path) -> int:
    """Redact actor-identifying fields for every event matching actor.

    Walks the active audit file and every rotated sibling so erasure is
    complete across the on disk history. Returns the total number of
    rows modified. Each file is rewritten atomically via tempfile +
    os.replace so a crash mid redaction leaves either the original or
    the fully redacted file, never a half written one.
    """
    total = 0
    with _redact_lock:
        for fpath in _rotated_paths(path):
            total += _redact_file(fpath, "actor", actor, _REDACT_FIELDS)
    return total


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
