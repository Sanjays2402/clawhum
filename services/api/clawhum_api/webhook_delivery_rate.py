"""Per-workspace per-hook outbound webhook delivery rate cap.

Why this exists
---------------
The existing webhook stack already enforces SSRF safety, HTTPS-only
transport, HMAC signing, retries with backoff, and a consecutive
failure circuit breaker. What it does not bound is *delivery rate*:
a runaway producer inside the workspace (a debugging script, a
mis-configured batch job, a single match endpoint hammered by a
test suite) can fan out thousands of events per minute to a single
customer endpoint. Enterprise receivers (banks, CRMs, on-prem ITSM)
explicitly require a sender-side cap so a noisy producer cannot
trip their own ingress rate limits or be classified as an attack.

This module stores a single integer per workspace: the maximum
deliveries per minute *per individual webhook* the dispatcher will
attempt. A value of ``0`` disables the cap (current behavior, kept
as the default so existing tenants are not surprised). When the cap
is exceeded the dispatcher writes a synthetic delivery record with
``status=0`` and ``rate_limited=True`` and skips the HTTP request,
so the workspace audit/delivery log still shows the suppression.

The cap is per hook (not per tenant) because each receiver advertises
its own rate budget; the workspace owner picks the lowest budget
across their hooks. Cross hook fan out of a single event therefore
sees independent counters, which matches how receivers actually
account for rate.

Storage follows the same append-only JSONL last-writer-wins pattern
as ``webhook_policy``/``scope_policy``/``invite_domains`` so no new
infrastructure is required and multi-worker writers stay safe.

Tenant scoping is enforced both at the route layer (every reader and
mutator resolves ``current_tenant_id`` first) and again here on every
load; cross tenant reads are impossible.
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

# Hard ceiling so a fat-fingered admin cannot effectively disable the
# cap via a giant number; anything above this is clamped on write.
MAX_PER_MINUTE_CEILING = 10_000


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_per_minute: int
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_per_minute": self.max_per_minute,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    s = get_settings()
    p = getattr(s, "webhook_delivery_rate_path", None)
    if p is None:
        base = Path(getattr(s, "webhooks_path", Path("./data/webhooks.jsonl"))).parent
        p = base / "webhook_delivery_rate.jsonl"
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
                    mpm = int(row.get("max_per_minute") or 0)
                except (TypeError, ValueError):
                    mpm = 0
                if mpm < 0:
                    mpm = 0
                if mpm > MAX_PER_MINUTE_CEILING:
                    mpm = MAX_PER_MINUTE_CEILING
                out[tid] = Policy(
                    tenant_id=tid,
                    max_per_minute=mpm,
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
        return Policy(tenant_id=tenant_id, max_per_minute=0,
                      updated_at=0.0, updated_by="")
    return pol


def max_per_minute(tenant_id: str) -> int:
    return get_policy(tenant_id).max_per_minute


def set_policy(*, tenant_id: str, max_per_minute: int, updated_by: str) -> Policy:
    try:
        mpm = int(max_per_minute)
    except (TypeError, ValueError):
        raise ValueError("max_per_minute must be an integer")
    if mpm < 0:
        raise ValueError("max_per_minute must be >= 0")
    if mpm > MAX_PER_MINUTE_CEILING:
        raise ValueError(
            f"max_per_minute must be <= {MAX_PER_MINUTE_CEILING}"
        )
    row = Policy(
        tenant_id=tenant_id,
        max_per_minute=mpm,
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


class RateLimitedError(Exception):
    """Raised when a delivery would exceed the workspace per-hook cap.

    Surfaced to the dispatcher so it can record a synthetic delivery
    instead of making an HTTP request.
    """

    code = "webhook_delivery_rate_limited"

    def __init__(self, *, max_per_minute: int, observed: int):
        self.max_per_minute = max_per_minute
        self.observed = observed
        super().__init__(
            f"workspace policy caps webhook deliveries at "
            f"{max_per_minute} per minute per hook "
            f"(observed {observed} in last 60s)"
        )
