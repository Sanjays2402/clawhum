"""Per-workspace match result top-k cap.

Why this exists
---------------
``/match`` and ``/batch`` accept a caller-supplied ``top_k`` that
controls how many candidate rows the matcher returns. The matcher
itself happily honours large values: an attacker (or a careless mobile
client) can pin ``top_k=10000`` and force the worker to copy and
serialise the entire candidate set for every request, blowing up
response size and JSON-encoding cost while every other tenant in the
process waits.

The decoded-duration cap (``match_duration``) bounds embedding cost on
the *input* side; this cap bounds serialisation / network cost on the
*output* side. Together they keep one tenant from monopolising shared
matcher capacity.

A value of ``0`` means "no per-workspace cap" (default) so the feature
is strictly opt-in and cannot regress existing tenants. Storage mirrors
``match_duration`` / ``body_size``: append-only JSONL with last-writer
wins and an in-process cache that ``reset_cache()`` clears in tests.
Tenant scoping is enforced at both the store layer (every accessor
takes a ``tenant_id``) and the route layer (``current_tenant_id``);
cross-tenant reads or writes are impossible.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "Policy"] | None = None
_CACHE_PATH: Path | None = None

# Absolute ceiling so a fat-fingered admin cannot effectively disable
# the cap by typing a giant number. 1000 is well above any legitimate
# UI which typically renders 10-50 rows, yet still leaves head room for
# offline analysis pipelines that pull a larger candidate window.
MAX_TOP_K_CEILING = 1000


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_top_k: int
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_top_k": self.max_top_k,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    s = get_settings()
    p = getattr(s, "match_topk_policy_path", None)
    if p is None:
        base = Path(getattr(s, "webhooks_path", Path("./data/webhooks.jsonl"))).parent
        p = base / "match_topk_policy.jsonl"
    return Path(p)


def _load_locked() -> dict[str, Policy]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, Policy] = {}
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
                    mk = int(row.get("max_top_k") or 0)
                except (TypeError, ValueError):
                    mk = 0
                if mk < 0:
                    mk = 0
                if mk > MAX_TOP_K_CEILING:
                    mk = MAX_TOP_K_CEILING
                out[tid] = Policy(
                    tenant_id=tid,
                    max_top_k=mk,
                    updated_at=float(row.get("updated_at") or 0.0),
                    updated_by=str(row.get("updated_by") or ""),
                )
    _CACHE = out
    _CACHE_PATH = p
    return out


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def get_policy(tenant_id: str) -> Policy:
    with _LOCK:
        pol = _load_locked().get(tenant_id)
    if pol is None:
        return Policy(
            tenant_id=tenant_id,
            max_top_k=0,
            updated_at=0.0,
            updated_by="",
        )
    return pol


def max_top_k(tenant_id: str) -> int:
    return get_policy(tenant_id).max_top_k


def set_policy(*, tenant_id: str, max_top_k: int, updated_by: str) -> Policy:
    try:
        mk = int(max_top_k)
    except (TypeError, ValueError):
        raise ValueError("max_top_k must be an integer")
    if mk < 0:
        raise ValueError("max_top_k must be >= 0")
    if mk > MAX_TOP_K_CEILING:
        raise ValueError(f"max_top_k must be <= {MAX_TOP_K_CEILING}")
    row = Policy(
        tenant_id=tenant_id,
        max_top_k=mk,
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
