"""Per-workspace closure lifecycle (GDPR right-to-erasure / account wind-down).

An admin can schedule the workspace for closure with a configurable
grace period. During the grace window the workspace becomes
*read-only*: every mutating request returns HTTP 423 Locked with a
``workspace_closing`` detail and the ``finalize_at`` timestamp so SDKs
and operators can route the error to a "cancel closure or export now"
runbook. Reads, exports, audit history, and the closure routes
themselves keep working so the customer can still pull data out.

Once ``finalize_at`` elapses, the workspace transitions to ``closed``
and every authenticated request (including reads) returns HTTP 410
Gone. Re-opening a closed workspace is intentionally not supported:
data export must happen during the grace window, and post-closure
recovery is an operational ticket, not an API call. The closure
record is append-only so the timeline survives for audit review.

Storage uses the same JSONL append pattern as ``legal_hold`` and
``sso_store`` so there is no database dependency.
"""

from __future__ import annotations

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
_CACHE_PATH: Path | None = None
_CACHE_MTIME: float = 0.0
_CACHE_ROWS: list[dict[str, Any]] = []


MIN_GRACE_SECONDS = 60  # 1 minute, useful for tests and demos
MAX_GRACE_SECONDS = 30 * 24 * 3600  # 30 days
DEFAULT_GRACE_SECONDS = 7 * 24 * 3600  # 7 days


@dataclass(frozen=True)
class Closure:
    id: str
    tenant_id: str
    reason: str
    scheduled_at: float
    scheduled_by: str
    finalize_at: float
    cancelled_at: float | None = None
    cancelled_by: str | None = None

    def state(self, now: float | None = None) -> str:
        if self.cancelled_at is not None:
            return "cancelled"
        t = now if now is not None else time.time()
        if t >= self.finalize_at:
            return "closed"
        return "scheduled"

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "reason": self.reason,
            "scheduled_at": self.scheduled_at,
            "scheduled_by": self.scheduled_by,
            "finalize_at": self.finalize_at,
            "cancelled_at": self.cancelled_at,
            "cancelled_by": self.cancelled_by,
            "state": self.state(now),
        }


def _store_path() -> Path:
    s = get_settings()
    p = (s.ip_allowlist_path.parent / "workspace_closures.jsonl").resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def reset_cache() -> None:
    global _CACHE_PATH, _CACHE_MTIME, _CACHE_ROWS
    with _LOCK:
        _CACHE_PATH = None
        _CACHE_MTIME = 0.0
        _CACHE_ROWS = []


def _load_rows() -> list[dict[str, Any]]:
    global _CACHE_PATH, _CACHE_MTIME, _CACHE_ROWS
    path = _store_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        mtime = 0.0
    with _LOCK:
        if _CACHE_PATH == path and _CACHE_MTIME == mtime:
            return list(_CACHE_ROWS)
        rows: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        _CACHE_PATH = path
        _CACHE_MTIME = mtime
        _CACHE_ROWS = list(rows)
        return list(rows)


def _append_event(event: dict[str, Any]) -> None:
    path = _store_path()
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        global _CACHE_PATH, _CACHE_MTIME
        _CACHE_PATH = None
        _CACHE_MTIME = 0.0


def _replay(tenant_id: str) -> dict[str, Closure]:
    by_id: dict[str, Closure] = {}
    tid_filter = (tenant_id or "").strip().lower()
    for row in _load_rows():
        tid = str(row.get("tenant_id") or "").strip().lower()
        if not tid or tid != tid_filter:
            continue
        cid = str(row.get("id") or "")
        if not cid:
            continue
        kind = row.get("kind")
        if kind == "scheduled":
            by_id[cid] = Closure(
                id=cid,
                tenant_id=tid,
                reason=str(row.get("reason", "") or ""),
                scheduled_at=float(row.get("ts", 0.0) or 0.0),
                scheduled_by=str(row.get("actor", "") or ""),
                finalize_at=float(row.get("finalize_at", 0.0) or 0.0),
            )
        elif kind == "cancelled":
            existing = by_id.get(cid)
            if existing is None or existing.cancelled_at is not None:
                continue
            by_id[cid] = Closure(
                id=existing.id,
                tenant_id=existing.tenant_id,
                reason=existing.reason,
                scheduled_at=existing.scheduled_at,
                scheduled_by=existing.scheduled_by,
                finalize_at=existing.finalize_at,
                cancelled_at=float(row.get("ts", 0.0) or 0.0),
                cancelled_by=str(row.get("actor", "") or ""),
            )
    return by_id


def list_closures(tenant_id: str) -> list[Closure]:
    rows = list(_replay(tenant_id).values())
    rows.sort(key=lambda c: c.scheduled_at, reverse=True)
    return rows


def current_closure(tenant_id: str) -> Closure | None:
    """Return the closure that determines current state, if any.

    A closure is "current" if it is scheduled (not cancelled) and we
    have not seen a later cancellation for it. We pick the most recent
    not-cancelled record so a fresh schedule supersedes an older one.
    """
    actives = [
        c for c in _replay(tenant_id).values() if c.cancelled_at is None
    ]
    if not actives:
        return None
    actives.sort(key=lambda c: c.scheduled_at, reverse=True)
    return actives[0]


def status_for(tenant_id: str, *, now: float | None = None) -> dict[str, Any]:
    c = current_closure(tenant_id)
    if c is None:
        return {"tenant_id": tenant_id, "state": "active", "closure": None}
    return {
        "tenant_id": tenant_id,
        "state": c.state(now),
        "closure": c.to_dict(now),
    }


def schedule_closure(
    tenant_id: str,
    *,
    reason: str,
    actor: str,
    grace_seconds: int | None = None,
) -> Closure:
    tid = (tenant_id or "").strip().lower()
    if not tid:
        raise ValueError("tenant_id required")
    if not reason or not reason.strip():
        raise ValueError("reason required")
    if grace_seconds is None:
        grace_seconds = DEFAULT_GRACE_SECONDS
    if grace_seconds < MIN_GRACE_SECONDS or grace_seconds > MAX_GRACE_SECONDS:
        raise ValueError(
            f"grace_seconds must be between {MIN_GRACE_SECONDS} and "
            f"{MAX_GRACE_SECONDS}"
        )
    existing = current_closure(tid)
    if existing is not None and existing.state() != "cancelled":
        if existing.state() == "closed":
            raise ValueError("workspace already closed")
        raise ValueError("closure already scheduled; cancel it first")
    cid = secrets.token_urlsafe(12)
    now = time.time()
    finalize_at = now + float(grace_seconds)
    event = {
        "kind": "scheduled",
        "id": cid,
        "tenant_id": tid,
        "reason": reason.strip()[:1024],
        "ts": now,
        "actor": actor or "",
        "finalize_at": finalize_at,
    }
    _append_event(event)
    return Closure(
        id=cid,
        tenant_id=tid,
        reason=event["reason"],
        scheduled_at=now,
        scheduled_by=event["actor"],
        finalize_at=finalize_at,
    )


def cancel_closure(tenant_id: str, closure_id: str, *, actor: str) -> Closure:
    tid = (tenant_id or "").strip().lower()
    by_id = _replay(tid)
    existing = by_id.get(closure_id)
    if existing is None:
        raise KeyError(closure_id)
    if existing.cancelled_at is not None:
        raise ValueError("closure already cancelled")
    if existing.state() == "closed":
        raise ValueError("closure already finalized; cannot cancel")
    now = time.time()
    _append_event({
        "kind": "cancelled",
        "id": closure_id,
        "tenant_id": tid,
        "ts": now,
        "actor": actor or "",
    })
    return Closure(
        id=existing.id,
        tenant_id=existing.tenant_id,
        reason=existing.reason,
        scheduled_at=existing.scheduled_at,
        scheduled_by=existing.scheduled_by,
        finalize_at=existing.finalize_at,
        cancelled_at=now,
        cancelled_by=actor or "",
    )


# ---------------------------------------------------------------------------
# Enforcement helpers
# ---------------------------------------------------------------------------


# Paths that stay reachable even after a workspace is fully closed so
# the customer can finish exporting data, inspect the audit log, and
# pull the closure status. Match against the request path *prefix*.
READ_ALLOWED_WHEN_CLOSED = (
    "/health",
    "/metrics",
    "/workspace/closure",
    "/v1/workspace/closure",
    "/audit",
    "/v1/audit",
    "/privacy",
    "/v1/privacy",
    "/me",
    "/v1/me",
)

# Methods considered mutating; these are blocked during the scheduled
# grace window with 423 Locked.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Mutating endpoints that must keep working during the grace window so
# the admin can cancel the closure (and so MFA / re-auth flows keep
# working while they evaluate options).
MUTATING_ALLOWED_WHEN_SCHEDULED = (
    "/workspace/closure",
    "/v1/workspace/closure",
    "/mfa",
    "/v1/mfa",
    "/privacy",  # data export request, redaction
    "/v1/privacy",
)


def _path_starts_with(path: str, prefixes: tuple[str, ...]) -> bool:
    for p in prefixes:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def evaluate(
    *,
    tenant_id: str,
    method: str,
    path: str,
    now: float | None = None,
) -> tuple[int, str, dict[str, Any]] | None:
    """Return ``(status_code, detail, payload)`` if the request must be
    rejected because the workspace is closing or closed, else ``None``.

    The decision is cached-free: every call re-reads the (cached) JSONL
    so a freshly scheduled closure takes effect on the very next
    request.
    """
    closure = current_closure(tenant_id)
    if closure is None:
        return None
    state = closure.state(now)
    if state == "cancelled":
        return None
    body = closure.to_dict(now)
    if state == "closed":
        if _path_starts_with(path, READ_ALLOWED_WHEN_CLOSED):
            return None
        return (
            410,
            "workspace_closed: this workspace has been closed and is no longer reachable",
            body,
        )
    # state == "scheduled"
    if method.upper() not in MUTATING_METHODS:
        return None
    if _path_starts_with(path, MUTATING_ALLOWED_WHEN_SCHEDULED):
        return None
    return (
        423,
        "workspace_closing: this workspace is scheduled for closure and is read-only until cancelled",
        body,
    )
