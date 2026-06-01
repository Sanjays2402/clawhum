"""Per-workspace request body size cap.

Why this exists
---------------
Enterprise procurement reviews flag any API that accepts unbounded
request payloads as a denial-of-service and storage-cost risk. The
match and batch routes already enforce ad-hoc 413s on archive uploads,
but there is no platform-wide ceiling: a careless caller (or an
attacker who stole an API key) can POST a 500 MiB JSON document at
``/match`` and force the worker to buffer it before validation runs.

Each workspace can pin a single integer here: the largest request body
the API will accept on any chargeable endpoint. A value of ``0`` means
"no per-workspace cap" (the existing default), so this is strictly
opt-in and cannot regress existing tenants.

The middleware enforces the cap before the route runs, in two places:

  * If the client sent a ``Content-Length`` header that already
    exceeds the cap, we refuse with HTTP 413 without reading a byte.
  * Otherwise we wrap the receive channel and stream-count bytes; the
    moment the running total crosses the cap we abort with 413, so a
    chunked sender cannot lie about the length and force us to buffer
    the full payload.

Health, metrics, and admin readers are skipped so an admin who set
their own cap too tight can still raise it. The skip list mirrors the
budget middleware's so the two enforcement bands stay consistent.

Storage follows the same append-only JSONL last-writer-wins pattern as
``webhook_policy``/``webhook_delivery_rate``/``scope_policy`` so no new
infrastructure is required and multi-worker writers stay safe. Tenant
scoping is enforced at both the route layer (every reader and mutator
resolves ``current_tenant_id`` first) and the store layer; cross
tenant reads are impossible.
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
# the cap by typing a giant number. 256 MiB is well above any sane
# match clip (a few seconds of opus is < 1 MiB) and still leaves head
# room for batch archive uploads, which have their own tighter cap.
MAX_BYTES_CEILING = 256 * 1024 * 1024


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_bytes: int
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_bytes": self.max_bytes,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    s = get_settings()
    p = getattr(s, "body_size_policy_path", None)
    if p is None:
        base = Path(getattr(s, "webhooks_path", Path("./data/webhooks.jsonl"))).parent
        p = base / "body_size_policy.jsonl"
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
                    mb = int(row.get("max_bytes") or 0)
                except (TypeError, ValueError):
                    mb = 0
                if mb < 0:
                    mb = 0
                if mb > MAX_BYTES_CEILING:
                    mb = MAX_BYTES_CEILING
                out[tid] = Policy(
                    tenant_id=tid,
                    max_bytes=mb,
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
        return Policy(tenant_id=tenant_id, max_bytes=0,
                      updated_at=0.0, updated_by="")
    return pol


def max_bytes(tenant_id: str) -> int:
    return get_policy(tenant_id).max_bytes


def set_policy(*, tenant_id: str, max_bytes: int, updated_by: str) -> Policy:
    try:
        mb = int(max_bytes)
    except (TypeError, ValueError):
        raise ValueError("max_bytes must be an integer")
    if mb < 0:
        raise ValueError("max_bytes must be >= 0")
    if mb > MAX_BYTES_CEILING:
        raise ValueError(
            f"max_bytes must be <= {MAX_BYTES_CEILING}"
        )
    row = Policy(
        tenant_id=tenant_id,
        max_bytes=mb,
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
