"""Per-workspace override for the outbound webhook retry envelope.

Why this exists
---------------
Enterprise procurement reviews routinely ask: "how many delivery
attempts will you make before giving up on our endpoint, and can we
control that per workspace?" Until now the answer was the single
global setting ``webhook_max_attempts``, which is fine for one
deployment but unworkable for a multi-tenant SaaS: a sandbox
tenant typically wants ``1`` so a broken receiver fails fast and a
human looks at it, while a production tenant with a flaky on-prem
ITSM endpoint wants a longer retry budget so transient blips
self-heal without paging anyone.

This module lets an admin pin a per-workspace attempt count
without shipping a config change. The deployment-wide
``webhook_max_attempts`` becomes the default for tenants that have
not opted in. Pairs with the existing
``webhook_auto_disable_policy`` (failure streak before the breaker
opens) and ``webhook_delivery_rate`` (per-hook rate ceiling) so a
workspace owner can shape the full outbound retry envelope from
the admin console.

Semantics
---------
* The default is ``webhook_max_attempts`` from global settings.
  Existing tenants behave exactly as before until an admin opts in
  to a different value.
* ``max_attempts >= 1``. Zero would mean "never try", which is
  identical to disabling the hook and would silently swallow
  events, so we reject it with 400 at the route layer.
* ``MAX_ATTEMPTS_CEILING`` is a hard ceiling so a typo cannot
  request billions of attempts and effectively turn the dispatcher
  into a denial-of-service tool aimed at a customer endpoint.
* Cross-tenant safety: every read takes ``tenant_id`` and never
  inspects other tenants' rows.
* Storage follows the same append-only JSONL last-writer-wins
  pattern used by ``webhook_auto_disable_policy`` and
  ``webhook_delivery_rate`` so multi-worker writers stay safe with
  no new infrastructure.
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

# Hard ceiling. With exponential backoff capped at 8s per sleep, 12
# attempts already covers ~1 minute of wall-clock retries, which is
# the practical envelope before the request would be re-fired by the
# event source anyway. Anything bigger is almost always a typo.
MAX_ATTEMPTS_CEILING = 12


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_attempts: int
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_attempts": self.max_attempts,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    s = get_settings()
    p = getattr(s, "webhook_max_attempts_policy_path", None)
    if p is None:
        base = Path(getattr(s, "webhooks_path", Path("./data/webhooks.jsonl"))).parent
        p = base / "webhook_max_attempts_policy.jsonl"
    return Path(p)


def _global_default() -> int:
    raw = int(getattr(get_settings(), "webhook_max_attempts", 3) or 0)
    return max(1, min(MAX_ATTEMPTS_CEILING, raw))


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
                    n = int(row.get("max_attempts") or 0)
                except (TypeError, ValueError):
                    continue
                if n < 1:
                    # Defensive: drop legacy/corrupt rows that would
                    # silently drop events. Re-saving via the route
                    # rejects this at 400 before it ever lands here.
                    continue
                if n > MAX_ATTEMPTS_CEILING:
                    n = MAX_ATTEMPTS_CEILING
                out[tid] = Policy(
                    tenant_id=tid,
                    max_attempts=n,
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


def effective_max_attempts(tenant_id: str) -> int:
    """Return the attempt cap actually enforced for ``tenant_id``.

    Resolution order:
      * Explicit policy row, clamped to [1, MAX_ATTEMPTS_CEILING].
      * Otherwise the global ``webhook_max_attempts`` default.

    Always at least 1 so an event is never silently dropped.
    """
    pol = get_policy(tenant_id)
    if pol is None:
        return _global_default()
    return max(1, min(MAX_ATTEMPTS_CEILING, pol.max_attempts))


def set_policy(*, tenant_id: str, max_attempts: int, updated_by: str) -> Policy:
    try:
        n = int(max_attempts)
    except (TypeError, ValueError):
        raise ValueError("max_attempts must be an integer")
    if n < 1:
        raise ValueError("max_attempts must be >= 1")
    if n > MAX_ATTEMPTS_CEILING:
        raise ValueError(
            f"max_attempts must be <= {MAX_ATTEMPTS_CEILING}"
        )
    row = Policy(
        tenant_id=tenant_id,
        max_attempts=n,
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
