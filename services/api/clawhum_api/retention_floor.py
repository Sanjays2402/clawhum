"""Per-workspace minimum-days floor for the data retention policy.

Why this exists
---------------
``retention`` already lets a workspace admin set per-category TTLs
(history, feedback, audit, webhook_deliveries). What it does NOT
prevent is a future admin (rushed, careless, or compromised) coming
back and shrinking ``audit_days`` from 365 to 7 right before deleting
evidence of misuse. SOC2 CC7.2 and ISO 27001 A.12.4.1 explicitly
require that audit log retention be defended against insider
tampering, and DPAs almost always pin a contractual floor for
operational logs.

This module lets the workspace itself pin a minimum number of days
per category. Once pinned:

* ``PUT /retention`` rejects any request that would set a category's
  TTL to a positive value below the floor with HTTP 400
  ``retention_floor_violation`` so the operator sees a clear reason.
* ``0`` (keep forever) is always allowed regardless of floor, because
  it strictly increases retention. The floor is a "no shorter than"
  constraint, not a "must be set" constraint.
* Reducing or removing the floor itself is a separate, MFA-gated
  admin action and is, of course, audit-logged like every other
  per-workspace policy in this service.

Semantics
---------
* All four floors default to ``0`` (no floor). Existing tenants are
  not affected until an admin explicitly opts in. Setting any floor
  to 0 removes the floor for that category.
* Each floor is bounded by ``MAX_FLOOR_DAYS`` to guard against typos
  that would effectively brick the retention form.
* Cross-tenant safety: every read takes ``tenant_id`` and never
  inspects other tenants' rows. Tenant A's floor has zero effect on
  tenant B.
* Storage follows the same append-only JSONL last-writer-wins pattern
  as ``retention``/``pat_min_requirements``/``webhook_max_attempts_policy``
  so multi-worker writers stay safe with no new infrastructure.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

from .retention import POLICY_FIELDS

_LOCK = Lock()
_CACHE: dict[str, "FloorPolicy"] | None = None
_CACHE_PATH: Path | None = None

# Mirror retention.py's own ceiling. Above this, the upstream policy
# field validation would reject the value anyway.
MAX_FLOOR_DAYS = 3650


@dataclass(frozen=True)
class FloorPolicy:
    tenant_id: str
    history_days: int = 0
    feedback_days: int = 0
    audit_days: int = 0
    webhook_deliveries_days: int = 0
    updated_at: float = 0.0
    updated_by: str = ""

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "history_days": self.history_days,
            "feedback_days": self.feedback_days,
            "audit_days": self.audit_days,
            "webhook_deliveries_days": self.webhook_deliveries_days,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    def floor_for(self, category: str) -> int:
        return int(getattr(self, f"{category}_days", 0) or 0)

    @property
    def empty(self) -> bool:
        return (
            self.history_days == 0
            and self.feedback_days == 0
            and self.audit_days == 0
            and self.webhook_deliveries_days == 0
        )


def _path() -> Path:
    s = get_settings()
    p = getattr(s, "retention_floor_path", None)
    if p is None:
        base = Path(getattr(s, "ip_allowlist_path", Path("./data/ip_allowlist.jsonl"))).parent
        p = base / "retention_floor.jsonl"
    return Path(p)


def _coerce_days(raw, *, field: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")
    if n < 0:
        raise ValueError(f"{field} must be >= 0")
    if n > MAX_FLOOR_DAYS:
        raise ValueError(f"{field} must be <= {MAX_FLOOR_DAYS}")
    return n


def _load_locked() -> dict[str, FloorPolicy]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, FloorPolicy] = {}
    if p.exists():
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
                try:
                    rec = FloorPolicy(
                        tenant_id=tid,
                        history_days=max(0, min(MAX_FLOOR_DAYS, int(row.get("history_days") or 0))),
                        feedback_days=max(0, min(MAX_FLOOR_DAYS, int(row.get("feedback_days") or 0))),
                        audit_days=max(0, min(MAX_FLOOR_DAYS, int(row.get("audit_days") or 0))),
                        webhook_deliveries_days=max(
                            0,
                            min(MAX_FLOOR_DAYS, int(row.get("webhook_deliveries_days") or 0)),
                        ),
                        updated_at=float(row.get("updated_at") or 0.0),
                        updated_by=str(row.get("updated_by") or ""),
                    )
                except (TypeError, ValueError):
                    continue
                out[tid] = rec
    _CACHE = out
    _CACHE_PATH = p
    return out


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def get_floor(tenant_id: str) -> FloorPolicy:
    with _LOCK:
        existing = _load_locked().get(tenant_id)
    return existing or FloorPolicy(tenant_id=tenant_id)


def set_floor(
    *,
    tenant_id: str,
    history_days: int,
    feedback_days: int,
    audit_days: int,
    webhook_deliveries_days: int,
    updated_by: str,
) -> FloorPolicy:
    h = _coerce_days(history_days, field="history_days")
    f = _coerce_days(feedback_days, field="feedback_days")
    a = _coerce_days(audit_days, field="audit_days")
    w = _coerce_days(webhook_deliveries_days, field="webhook_deliveries_days")
    row = FloorPolicy(
        tenant_id=tenant_id,
        history_days=h,
        feedback_days=f,
        audit_days=a,
        webhook_deliveries_days=w,
        updated_at=time.time(),
        updated_by=(updated_by or "").strip()[:64] or "unknown",
    )
    with _LOCK:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row.to_dict()) + "\n")
        store = _load_locked()
        store[tenant_id] = row
    return row


def assert_policy_meets_floor(
    *,
    tenant_id: str,
    history_days: int,
    feedback_days: int,
    audit_days: int,
    webhook_deliveries_days: int,
) -> None:
    """Raise ``ValueError`` if any requested days fall below the floor.

    A value of 0 (keep forever) is always allowed because it strictly
    increases retention. The check only fires for positive values
    below the corresponding floor.
    """
    floor = get_floor(tenant_id)
    if floor.empty:
        return
    candidates = {
        "history_days": history_days,
        "feedback_days": feedback_days,
        "audit_days": audit_days,
        "webhook_deliveries_days": webhook_deliveries_days,
    }
    violations: list[str] = []
    for field, requested in candidates.items():
        try:
            n = int(requested)
        except (TypeError, ValueError):
            continue
        floor_val = int(getattr(floor, field) or 0)
        if floor_val <= 0:
            continue
        if n == 0:
            # 0 means "keep forever", which always satisfies a floor.
            continue
        if n < floor_val:
            violations.append(f"{field} requested {n} but floor is {floor_val}")
    if violations:
        raise ValueError("; ".join(violations))


# Sanity-check that we cover every retention category. If a new
# category is added to ``retention.POLICY_FIELDS`` we want the build
# to fail loudly here so the floor gets extended at the same time.
assert set(POLICY_FIELDS) == {
    "history",
    "feedback",
    "audit",
    "webhook_deliveries",
}, "retention_floor must be updated to track all retention categories"
