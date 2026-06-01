"""Per-workspace concurrent active PAT cap.

Why this exists
---------------
Enterprise procurement reviews routinely ask: "how many machine
credentials can a single workspace hold at once, and who controls
that ceiling?". A workspace with no upper bound on live PATs has
unbounded blast radius if one admin account is compromised, and
encourages credential sprawl that nobody audits.

This module lets a workspace admin pin ``max_active`` (the largest
number of non-revoked, non-expired PATs the workspace is ever allowed
to hold). When a mint at ``POST /keys`` would push the live count over
the cap, the mint is rejected with a structured 429 so the operator
notices instead of silently exceeding policy.

Semantics
---------
* ``max_active = 0`` means "no cap" so existing customers are not
  broken by enabling this module.
* The active set is whatever ``pat_store.live_for_tenant`` returns,
  minus expired tokens. Tokens revoked in the future will free a slot
  immediately.
* Cross-tenant safety: every read and assertion takes ``tenant_id``
  and never inspects other tenants' rows.
* Storage follows the same append-only JSONL last-writer-wins pattern
  as ``scope_policy`` and ``body_size`` so multi-worker writers stay
  safe with no new infra.
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

# Hard ceiling so a typo cannot ask for billions; 10k is well above
# any realistic enterprise mint count and keeps JSONL scans bounded.
MAX_CAP = 10_000


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_active: int  # 0 means "no cap"
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_active": self.max_active,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().pat_concurrency_path)


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
                    cap = max(0, min(MAX_CAP, int(row.get("max_active") or 0)))
                except (TypeError, ValueError):
                    continue
                out[tid] = Policy(
                    tenant_id=tid,
                    max_active=cap,
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


def get_policy(tenant_id: str) -> Policy | None:
    with _LOCK:
        return _load_locked().get(tenant_id)


def max_active(tenant_id: str) -> int:
    p = get_policy(tenant_id)
    return p.max_active if p else 0


def has_cap(tenant_id: str) -> bool:
    return max_active(tenant_id) > 0


def set_policy(*, tenant_id: str, max_active: int, updated_by: str) -> Policy:
    """Replace the workspace concurrent-PAT cap. Pass 0 to clear."""
    cap = max(0, min(MAX_CAP, int(max_active or 0)))
    row = Policy(
        tenant_id=tenant_id,
        max_active=cap,
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


def count_active(tenant_id: str, *, now: float | None = None) -> int:
    """Count live, non-expired PATs scoped to this tenant."""
    # Import inline to keep pat_concurrency free of pat_store at load.
    from . import pat_store

    t = now if now is not None else time.time()
    count = 0
    for tok in pat_store.live_for_tenant(tenant_id):
        if tok.is_expired(t):
            continue
        count += 1
    return count


class PatConcurrencyExceeded(ValueError):
    """Raised when a mint would push a workspace past its PAT cap."""

    def __init__(self, tenant_id: str, live: int, max_active: int):
        self.tenant_id = tenant_id
        self.live = live
        self.max_active = max_active
        super().__init__(
            f"workspace pat cap exceeded: {live}/{max_active}"
        )


def assert_capacity(tenant_id: str, *, now: float | None = None) -> None:
    """Raise PatConcurrencyExceeded when a new mint would breach the cap.

    No-op when ``max_active`` is 0 (no cap). Safe to call on every
    mint; cost is one in-process dict lookup plus a pass over the
    tenant's live PATs.
    """
    cap = max_active(tenant_id)
    if cap <= 0:
        return
    live = count_active(tenant_id, now=now)
    if live >= cap:
        raise PatConcurrencyExceeded(tenant_id, live, cap)
