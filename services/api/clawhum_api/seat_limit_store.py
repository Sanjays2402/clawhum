"""Per-workspace member seat cap.

Enterprise contracts price by seats. When the contract says "50 seats"
the platform must refuse seat 51 and tell the buyer to upgrade, not
silently overflow. This module owns the cap configuration: one
optional integer per tenant, persisted append only so audit reviewers
can see who changed the limit and when.

Storage matches the JSONL pattern used elsewhere (members, PATs,
webhooks). A missing or zero limit means "unlimited" so existing
workspaces keep working without a forced migration.

Enforcement lives in member_store.invite and member_store.create_active.
Both call ``check_capacity(tenant_id)`` before persisting a new row;
on overflow they raise ``SeatLimitExceededError`` and routes translate
that to HTTP 402 Payment Required, the conventional license-exceeded
status code. Re-activating an existing tombstoned row does not consume
a fresh seat; only net-new active+invited rows do.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings


_LOCK = Lock()


class SeatLimitExceededError(Exception):
    """Raised when an invite or auto-join would exceed the seat cap.

    The exception carries the current count and configured limit so
    the API layer can surface a structured error body for the admin
    console to render an "upgrade your plan" affordance.
    """

    def __init__(self, *, tenant_id: str, current: int, limit: int) -> None:
        super().__init__(
            f"seat limit reached for workspace {tenant_id!r}: "
            f"{current}/{limit} seats used"
        )
        self.tenant_id = tenant_id
        self.current = current
        self.limit = limit


@dataclass(frozen=True)
class SeatLimit:
    tenant_id: str
    limit: int  # 0 means unlimited
    updated_by: str
    updated_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "limit": self.limit,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


def _path() -> Path:
    p = Path(get_settings().seat_limits_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.touch()
    return p


def _append(record: dict[str, Any]) -> None:
    path = _path()
    line = json.dumps(record, separators=(",", ":"), sort_keys=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _load_all() -> dict[str, SeatLimit]:
    """Last-writer-wins per tenant."""
    path = _path()
    rows: dict[str, SeatLimit] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = (rec.get("tenant_id") or "").strip().lower()
            if not tid:
                continue
            try:
                limit = int(rec.get("limit", 0))
            except (TypeError, ValueError):
                continue
            if limit < 0:
                continue
            rows[tid] = SeatLimit(
                tenant_id=tid,
                limit=limit,
                updated_by=str(rec.get("updated_by", "")),
                updated_at=float(rec.get("updated_at", 0.0)),
            )
    return rows


def get(tenant_id: str) -> SeatLimit | None:
    tenant_id = (tenant_id or "").strip().lower()
    if not tenant_id:
        return None
    return _load_all().get(tenant_id)


def get_limit(tenant_id: str) -> int:
    """Return the configured limit, or 0 for unlimited."""
    rec = get(tenant_id)
    return rec.limit if rec is not None else 0


def set_limit(
    *,
    tenant_id: str,
    limit: int,
    updated_by: str,
    now: float | None = None,
) -> SeatLimit:
    tenant_id = (tenant_id or "").strip().lower()
    if not tenant_id:
        raise ValueError("tenant_id is required")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("limit must be a non-negative integer")
    if limit < 0:
        raise ValueError("limit must be a non-negative integer")
    # Upper bound: keep nonsense out of the log; the largest enterprise
    # tier today is well under this.
    if limit > 1_000_000:
        raise ValueError("limit is unreasonably large")
    rec = SeatLimit(
        tenant_id=tenant_id,
        limit=int(limit),
        updated_by=updated_by or "unknown",
        updated_at=time.time() if now is None else now,
    )
    _append(asdict(rec))
    return rec


def consumed_count(tenant_id: str) -> int:
    """Seats currently occupying the workspace.

    Active members count. Pending invites count, because once
    accepted they immediately occupy a seat and the cap should
    reject sending more invites than the contract allows. Revoked
    rows do not count. Importing here avoids a circular import at
    module load time.
    """
    from . import member_store

    counts = member_store.count_for_tenant(tenant_id)
    return int(counts.get("active", 0)) + int(counts.get("invited", 0))


def check_capacity(tenant_id: str) -> None:
    """Raise SeatLimitExceededError if a new seat would overflow.

    Called from member_store.invite and member_store.create_active.
    A limit of 0 means unlimited, the default for workspaces with
    no contract attached.
    """
    limit = get_limit(tenant_id)
    if limit <= 0:
        return
    current = consumed_count(tenant_id)
    if current >= limit:
        raise SeatLimitExceededError(
            tenant_id=tenant_id, current=current, limit=limit
        )


def reset_for_tests() -> None:
    path = _path()
    with _LOCK:
        path.write_text("", encoding="utf-8")
