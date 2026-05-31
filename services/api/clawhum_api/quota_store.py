"""Per-workspace quota plans.

Per-key rate limiting alone is not enough for enterprise contracts.
A workspace can mint many API keys; each individual key may stay under
its own RPM, but the *aggregate* traffic still needs a ceiling we sold
the customer on (the "plan"). This module stores a single ``Plan`` per
tenant covering:

* ``rpm_ceiling`` -- maximum requests-per-minute aggregated across every
  key and IP for that workspace. ``0`` means "no workspace ceiling, fall
  back to per-key limits only" so existing tenants are unaffected.
* ``daily_quota`` -- rolling 24h request budget for the workspace, also
  aggregated. ``0`` means unlimited.
* ``plan`` -- human label ("free", "team", "enterprise"). Cosmetic only;
  the numbers are what the middleware enforces.

Storage is the same JSONL append-only pattern used by IP allowlist,
PATs, members, etc. so no new infra is required. Reads are cached in
process and invalidated on write. The middleware checks the cache on
every request and is O(1) per call.

We deliberately keep this independent of FastAPI so the middleware can
import it without circular imports.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "Plan"] | None = None
_CACHE_PATH: Path | None = None

# Plans we expose to admins. The numbers are defaults at creation time;
# admins can override per workspace.
PLAN_DEFAULTS: dict[str, tuple[int, int]] = {
    # name: (rpm_ceiling, daily_quota). 0 means unlimited.
    "free": (60, 5_000),
    "team": (600, 100_000),
    "business": (3_000, 1_000_000),
    "enterprise": (0, 0),
    "custom": (0, 0),
}

VALID_PLANS = frozenset(PLAN_DEFAULTS.keys())


@dataclass(frozen=True)
class Plan:
    tenant_id: str
    plan: str
    rpm_ceiling: int
    daily_quota: int
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "plan": self.plan,
            "rpm_ceiling": self.rpm_ceiling,
            "daily_quota": self.daily_quota,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _default_plan(tenant_id: str) -> Plan:
    rpm, day = PLAN_DEFAULTS["enterprise"]
    return Plan(
        tenant_id=tenant_id,
        plan="enterprise",
        rpm_ceiling=rpm,
        daily_quota=day,
        updated_at=0.0,
        updated_by="system",
    )


def _path() -> Path:
    return get_settings().quota_path


def reset_cache() -> None:
    """Drop the in-process cache. Tests and lifespan reuse this."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _load() -> dict[str, Plan]:
    """Return the parsed plan map, populating the cache if needed."""
    global _CACHE, _CACHE_PATH
    path = _path()
    if _CACHE is not None and _CACHE_PATH == path:
        return _CACHE
    out: dict[str, Plan] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            tid = str(rec.get("tenant_id") or "").lower()
            if not tid:
                continue
            out[tid] = Plan(
                tenant_id=tid,
                plan=str(rec.get("plan") or "custom"),
                rpm_ceiling=max(0, int(rec.get("rpm_ceiling") or 0)),
                daily_quota=max(0, int(rec.get("daily_quota") or 0)),
                updated_at=float(rec.get("updated_at") or 0.0),
                updated_by=str(rec.get("updated_by") or "system"),
            )
    _CACHE = out
    _CACHE_PATH = path
    return out


def get_plan(tenant_id: str) -> Plan:
    """Return the workspace plan, or the unlimited default if none set."""
    tid = (tenant_id or "").lower()
    with _LOCK:
        plans = _load()
        return plans.get(tid) or _default_plan(tid or "anonymous")


def set_plan(
    *,
    tenant_id: str,
    plan: str,
    rpm_ceiling: int,
    daily_quota: int,
    actor: str,
) -> Plan:
    """Upsert a plan. Returns the persisted record."""
    tid = (tenant_id or "").lower()
    if not tid:
        raise ValueError("tenant_id required")
    if plan not in VALID_PLANS:
        raise ValueError(f"unknown plan: {plan}")
    rpm = max(0, int(rpm_ceiling))
    day = max(0, int(daily_quota))
    rec = Plan(
        tenant_id=tid,
        plan=plan,
        rpm_ceiling=rpm,
        daily_quota=day,
        updated_at=time.time(),
        updated_by=actor or "unknown",
    )
    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rewrite the whole file from the merged map. This keeps the
        # newest write authoritative without an O(n) scan on every read
        # and matches the JSONL "latest wins" pattern used elsewhere.
        plans = _load()
        plans[tid] = rec
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for p in plans.values():
                f.write(json.dumps(p.to_dict(), separators=(",", ":"), sort_keys=True))
                f.write("\n")
        tmp.replace(path)
        global _CACHE
        _CACHE = plans
    return rec


def list_plans() -> list[Plan]:
    with _LOCK:
        return sorted(_load().values(), key=lambda p: p.tenant_id)
