"""Per-workspace cap on the number of registered webhook destinations.

Why this exists
---------------
Enterprise procurement reviews routinely ask: "how many outbound
HTTP destinations can a single workspace register, and who controls
that ceiling?". A workspace with no upper bound on live webhook
destinations encourages credential sprawl, expands SSRF blast
radius, and makes a tenant a noisy neighbour against the delivery
worker. Operators want to pin a low number for production tenants
and let internal sandbox tenants go higher.

Historically ``POST /webhooks`` enforced a hard-coded soft cap of
20 per tenant. That ceiling is now a per-workspace policy stored in
this module; the global hard ceiling is preserved as ``MAX_CAP`` so
a typo cannot ask for billions.

Semantics
---------
* The default cap is ``DEFAULT_CAP`` (20). Existing tenants behave
  exactly as before until an admin opts in to a different value.
* ``max_active = 0`` means "no per-workspace cap" but the global
  ``MAX_CAP`` is still enforced so storage scans stay bounded.
* The active set is the workspace's currently-live hooks (not
  deleted). Deleting a hook frees a slot immediately.
* Cross-tenant safety: every read and assertion takes ``tenant_id``
  and never inspects other tenants' rows.
* Storage follows the same append-only JSONL last-writer-wins
  pattern used by ``pat_concurrency`` and ``body_size`` so multi
  worker writers stay safe with no new infrastructure.
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

# Global hard ceiling. No tenant may ever exceed this even with an
# explicit policy, so the JSONL store and delivery worker remain
# bounded. 500 is well above any realistic enterprise integration
# count (most buyers ship with single digits).
MAX_CAP = 500

# Default cap applied when no policy row exists. Matches the legacy
# hard-coded soft cap removed from ``routes/webhooks.create_webhook``
# so existing tenants are not silently allowed to grow unbounded.
DEFAULT_CAP = 20


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_active: int  # 0 means "no per-workspace cap"
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
    return Path(get_settings().webhook_destination_cap_path)


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


def effective_cap(tenant_id: str) -> int:
    """Return the cap that will actually be enforced for ``tenant_id``.

    Resolution order:
      * Explicit policy row, even ``max_active = 0`` (no cap).
      * Otherwise ``DEFAULT_CAP`` so existing tenants keep the
        legacy soft limit.

    Always bounded by ``MAX_CAP``.
    """
    p = get_policy(tenant_id)
    if p is None:
        return DEFAULT_CAP
    if p.max_active <= 0:
        # Explicit opt-out from the per-workspace cap; still bounded
        # globally so callers never see an unbounded value.
        return MAX_CAP
    return min(MAX_CAP, p.max_active)


def set_policy(*, tenant_id: str, max_active: int, updated_by: str) -> Policy:
    """Replace the workspace cap. Pass 0 to opt out of the cap entirely."""
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


class WebhookDestinationCapExceeded(ValueError):
    """Raised when registering one more destination would breach policy."""

    def __init__(self, tenant_id: str, live: int, max_active: int):
        self.tenant_id = tenant_id
        self.live = live
        self.max_active = max_active
        super().__init__(
            f"workspace webhook destination cap exceeded: {live}/{max_active}"
        )


def assert_capacity(tenant_id: str, *, live_count: int) -> None:
    """Raise when registering a new destination would breach the cap.

    Caller supplies ``live_count`` (the number of non-deleted hooks
    for this tenant) so we do not need to import the routes module
    and create a cycle.
    """
    cap = effective_cap(tenant_id)
    if cap <= 0:
        return
    if live_count >= cap:
        raise WebhookDestinationCapExceeded(tenant_id, live_count, cap)
