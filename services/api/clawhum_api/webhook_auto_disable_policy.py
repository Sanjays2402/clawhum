"""Per-workspace override for the webhook circuit-breaker threshold.

Why this exists
---------------
Enterprise procurement reviews routinely ask: "how many failed
deliveries does it take before our webhook endpoint is
auto-disabled, and can we control it per workspace?". Until now
the threshold lived in a single global setting
(``webhook_auto_disable_threshold``), which is fine for a small
deployment but unworkable for a multi-tenant SaaS: a noisy
sandbox tenant wants a low number so a broken receiver pauses
fast, while a production tenant with bursty downstream outages
wants more headroom before the integration falls silent.

This module lets an admin pin a per-workspace threshold without
shipping a config change. The global setting becomes the default
for tenants that have not opted in.

Semantics
---------
* The default threshold is ``webhook_auto_disable_threshold`` from
  global settings. Existing tenants behave exactly as before until
  an admin opts in to a different value.
* ``threshold = 0`` disables the breaker for that workspace and a
  hook must be paused manually. Operators may still want this for
  on-prem integrations they monitor out of band.
* ``MAX_THRESHOLD`` is a hard ceiling so a typo cannot ask for
  billions and effectively disable the breaker by accident.
* Cross-tenant safety: every read and assertion takes ``tenant_id``
  and never inspects other tenants' rows.
* Storage follows the same append-only JSONL last-writer-wins
  pattern used by ``webhook_destination_cap`` and ``body_size``
  so multi worker writers stay safe with no new infrastructure.
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

# Hard ceiling. Enterprises that genuinely want "never auto-disable"
# should set threshold = 0 (manual pause) rather than asking for a
# very large number. 10_000 keeps the streak scan bounded.
MAX_THRESHOLD = 10_000


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    threshold: int  # 0 means "breaker disabled for this workspace"
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "threshold": self.threshold,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().webhook_auto_disable_policy_path)


def _global_default() -> int:
    raw = int(getattr(get_settings(), "webhook_auto_disable_threshold", 0) or 0)
    return max(0, min(MAX_THRESHOLD, raw))


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
                    th = max(0, min(MAX_THRESHOLD, int(row.get("threshold") or 0)))
                except (TypeError, ValueError):
                    continue
                out[tid] = Policy(
                    tenant_id=tid,
                    threshold=th,
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


def effective_threshold(tenant_id: str) -> int:
    """Return the breaker threshold actually enforced for ``tenant_id``.

    Resolution order:
      * Explicit policy row, even ``threshold = 0`` (breaker off).
      * Otherwise the global ``webhook_auto_disable_threshold``.

    Always bounded by ``MAX_THRESHOLD``.
    """
    p = get_policy(tenant_id)
    if p is None:
        return _global_default()
    return min(MAX_THRESHOLD, max(0, p.threshold))


def set_policy(*, tenant_id: str, threshold: int, updated_by: str) -> Policy:
    """Replace the workspace threshold.

    Pass ``threshold = 0`` to opt out of the circuit breaker for
    this workspace (manual pause only).
    """
    th = max(0, min(MAX_THRESHOLD, int(threshold or 0)))
    row = Policy(
        tenant_id=tenant_id,
        threshold=th,
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
