"""Per-workspace monthly spend cap.

Finance and procurement teams will not sign without a hard ceiling on
billable consumption. Rate limits bound the *rate*; budgets bound the
*month*. Both are needed: a workspace under its RPM ceiling can still
blow past the contracted monthly volume in a few days of steady use.

A ``Budget`` per tenant covers:

* ``monthly_cap`` -- maximum chargeable requests per calendar-rolling
  30 day window. ``0`` means "no monthly ceiling" (default), so
  existing workspaces are unaffected until an admin opts in.
* ``soft_threshold_pct`` -- warning point reported in /usage so the
  client UI and outbound monitoring can react before the hard stop.
  Defaults to 80; set to 0 to disable the warning.
* ``hard_stop`` -- when ``True``, chargeable requests beyond the cap
  return HTTP 402 with a structured error so integrations can fail
  closed. When ``False``, the cap is observed but not enforced
  (audit-only mode finance teams ask for during rollout).

Storage follows the same append-only JSONL latest-wins pattern used by
every other per-tenant store. Reads are cached in process and the
cache is invalidated on write. Independent of FastAPI so the budget
middleware can import it without circular imports.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "Budget"] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class Budget:
    tenant_id: str
    monthly_cap: int          # 0 = unlimited
    soft_threshold_pct: int   # 0..100, 0 disables soft alert
    hard_stop: bool           # True = enforce 402, False = audit-only
    notes: str
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "monthly_cap": self.monthly_cap,
            "soft_threshold_pct": self.soft_threshold_pct,
            "hard_stop": self.hard_stop,
            "notes": self.notes,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _default_budget(tenant_id: str) -> Budget:
    return Budget(
        tenant_id=tenant_id,
        monthly_cap=0,
        soft_threshold_pct=80,
        hard_stop=True,
        notes="",
        updated_at=0.0,
        updated_by="system",
    )


def _path() -> Path:
    p = getattr(get_settings(), "budget_path", None)
    if p is None:
        p = Path("./data/budgets.jsonl")
    return Path(p)


def reset_cache() -> None:
    """Drop the in-process cache. Tests and lifespan reuse this."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _load() -> dict[str, Budget]:
    global _CACHE, _CACHE_PATH
    path = _path()
    if _CACHE is not None and _CACHE_PATH == path:
        return _CACHE
    out: dict[str, Budget] = {}
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
            soft = int(rec.get("soft_threshold_pct") or 0)
            soft = max(0, min(100, soft))
            out[tid] = Budget(
                tenant_id=tid,
                monthly_cap=max(0, int(rec.get("monthly_cap") or 0)),
                soft_threshold_pct=soft,
                hard_stop=bool(rec.get("hard_stop", True)),
                notes=str(rec.get("notes") or "")[:280],
                updated_at=float(rec.get("updated_at") or 0.0),
                updated_by=str(rec.get("updated_by") or "system"),
            )
    _CACHE = out
    _CACHE_PATH = path
    return out


def get_budget(tenant_id: str) -> Budget:
    """Return the workspace budget, or the unlimited default if none set."""
    tid = (tenant_id or "").lower()
    with _LOCK:
        budgets = _load()
        return budgets.get(tid) or _default_budget(tid or "anonymous")


def set_budget(
    *,
    tenant_id: str,
    monthly_cap: int,
    soft_threshold_pct: int,
    hard_stop: bool,
    notes: str,
    actor: str,
) -> Budget:
    """Upsert a budget. Returns the persisted record."""
    tid = (tenant_id or "").lower()
    if not tid:
        raise ValueError("tenant_id required")
    soft = max(0, min(100, int(soft_threshold_pct)))
    rec = Budget(
        tenant_id=tid,
        monthly_cap=max(0, int(monthly_cap)),
        soft_threshold_pct=soft,
        hard_stop=bool(hard_stop),
        notes=(notes or "")[:280],
        updated_at=time.time(),
        updated_by=actor or "unknown",
    )
    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        budgets = _load()
        budgets[tid] = rec
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for b in budgets.values():
                f.write(json.dumps(b.to_dict(), separators=(",", ":"), sort_keys=True))
                f.write("\n")
        tmp.replace(path)
        global _CACHE
        _CACHE = budgets
    return rec


def list_budgets() -> list[Budget]:
    with _LOCK:
        return sorted(_load().values(), key=lambda b: b.tenant_id)
