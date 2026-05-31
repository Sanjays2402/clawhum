"""Per-workspace legal hold (litigation hold / preservation order).

When at least one active hold exists for a tenant, every destructive
data operation against that tenant must short-circuit. Concretely
this blocks:

* ``retention.enforce_policy`` purge sweeps.
* ``DELETE /privacy/me`` audit/feedback redaction.
* ``DELETE /history/{id}`` tombstone writes.

Reads, exports, and audit appends are never blocked. A hold can be
released only by a workspace admin (with MFA), which records the
release time but never deletes the hold record so the timeline is
preserved for compliance review.

Storage uses the same JSONL append pattern as the audit log and
sso_store. One file per deployment, scoped on tenant_id, append only.
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


@dataclass(frozen=True)
class LegalHold:
    id: str
    tenant_id: str
    reason: str
    created_at: float
    created_by: str
    released_at: float | None = None
    released_by: str | None = None

    @property
    def active(self) -> bool:
        return self.released_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "released_at": self.released_at,
            "released_by": self.released_by,
            "active": self.active,
        }


class LegalHoldActive(Exception):
    def __init__(self, hold: "LegalHold"):
        super().__init__(f"legal hold {hold.id} active for tenant {hold.tenant_id}")
        self.hold = hold


def _store_path() -> Path:
    s = get_settings()
    p = (s.ip_allowlist_path.parent / "legal_holds.jsonl").resolve()
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


def _replay(tenant_id: str | None = None) -> dict[str, LegalHold]:
    by_id: dict[str, LegalHold] = {}
    tid_filter = (tenant_id or "").strip().lower() or None
    for row in _load_rows():
        tid = str(row.get("tenant_id") or "").strip().lower()
        if not tid:
            continue
        if tid_filter is not None and tid != tid_filter:
            continue
        hid = str(row.get("id") or "")
        if not hid:
            continue
        kind = row.get("kind")
        if kind == "placed":
            by_id[hid] = LegalHold(
                id=hid,
                tenant_id=tid,
                reason=str(row.get("reason", "") or ""),
                created_at=float(row.get("ts", 0.0) or 0.0),
                created_by=str(row.get("actor", "") or ""),
            )
        elif kind == "released":
            existing = by_id.get(hid)
            if existing is None or not existing.active:
                continue
            by_id[hid] = LegalHold(
                id=existing.id,
                tenant_id=existing.tenant_id,
                reason=existing.reason,
                created_at=existing.created_at,
                created_by=existing.created_by,
                released_at=float(row.get("ts", 0.0) or 0.0),
                released_by=str(row.get("actor", "") or ""),
            )
    return by_id


def list_holds(tenant_id: str) -> list[LegalHold]:
    holds = list(_replay(tenant_id).values())
    holds.sort(key=lambda h: h.created_at, reverse=True)
    return holds


def active_hold(tenant_id: str) -> LegalHold | None:
    actives = [h for h in _replay(tenant_id).values() if h.active]
    if not actives:
        return None
    actives.sort(key=lambda h: h.created_at)
    return actives[0]


def is_on_hold(tenant_id: str) -> bool:
    return active_hold(tenant_id) is not None


def assert_not_on_hold(tenant_id: str) -> None:
    h = active_hold(tenant_id)
    if h is not None:
        raise LegalHoldActive(h)


def place_hold(tenant_id: str, *, reason: str, actor: str) -> LegalHold:
    tid = (tenant_id or "").strip().lower()
    if not tid:
        raise ValueError("tenant_id required")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("reason required")
    if len(reason) > 1024:
        raise ValueError("reason too long")
    hid = "lh_" + secrets.token_hex(8)
    now = time.time()
    _append_event({
        "kind": "placed",
        "id": hid,
        "tenant_id": tid,
        "reason": reason,
        "actor": actor or "",
        "ts": now,
    })
    return LegalHold(
        id=hid, tenant_id=tid, reason=reason,
        created_at=now, created_by=actor or "",
    )


def release_hold(tenant_id: str, hold_id: str, *, actor: str) -> LegalHold:
    tid = (tenant_id or "").strip().lower()
    if not tid:
        raise ValueError("tenant_id required")
    current = _replay(tid).get(hold_id)
    if current is None:
        raise KeyError(hold_id)
    if not current.active:
        return current
    now = time.time()
    _append_event({
        "kind": "released",
        "id": hold_id,
        "tenant_id": tid,
        "actor": actor or "",
        "ts": now,
    })
    return LegalHold(
        id=current.id, tenant_id=current.tenant_id, reason=current.reason,
        created_at=current.created_at, created_by=current.created_by,
        released_at=now, released_by=actor or "",
    )
