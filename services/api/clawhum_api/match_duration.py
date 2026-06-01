"""Per-workspace match query duration cap.

Why this exists
---------------
``/match`` accepts arbitrary audio uploads and runs them through the
embedder + ANN index. A pathological caller (or a careless mobile app
left recording in a pocket) can submit a 30 minute "hum", which forces
the matcher to embed thousands of frames before returning. That bloats
worker latency for every other tenant sharing the process and inflates
storage if history is enabled.

The body-size cap caps *bytes*, which is a poor proxy for compute cost:
a 4 MiB Opus stream may decode to 25 minutes of audio. This module caps
the *decoded duration* in seconds, after ``load_audio`` has run, so the
ceiling is meaningful regardless of codec, sample rate, or bitrate.

A value of ``0`` means "no per-workspace cap" (default), so this is
strictly opt-in and cannot regress existing tenants. Storage mirrors
``body_size`` / ``webhook_policy``: append-only JSONL with last-writer
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
# the cap by typing a giant number. One hour is well above any
# legitimate hum query (real callers stay under 30s) yet still leaves
# head room for batch import workflows that pre-flight long clips.
MAX_DURATION_CEILING_SEC = 3600


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_duration_sec: int
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_duration_sec": self.max_duration_sec,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    s = get_settings()
    p = getattr(s, "match_duration_policy_path", None)
    if p is None:
        base = Path(getattr(s, "webhooks_path", Path("./data/webhooks.jsonl"))).parent
        p = base / "match_duration_policy.jsonl"
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
                    md = int(row.get("max_duration_sec") or 0)
                except (TypeError, ValueError):
                    md = 0
                if md < 0:
                    md = 0
                if md > MAX_DURATION_CEILING_SEC:
                    md = MAX_DURATION_CEILING_SEC
                out[tid] = Policy(
                    tenant_id=tid,
                    max_duration_sec=md,
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
            max_duration_sec=0,
            updated_at=0.0,
            updated_by="",
        )
    return pol


def max_duration_sec(tenant_id: str) -> int:
    return get_policy(tenant_id).max_duration_sec


def set_policy(*, tenant_id: str, max_duration_sec: int, updated_by: str) -> Policy:
    try:
        md = int(max_duration_sec)
    except (TypeError, ValueError):
        raise ValueError("max_duration_sec must be an integer")
    if md < 0:
        raise ValueError("max_duration_sec must be >= 0")
    if md > MAX_DURATION_CEILING_SEC:
        raise ValueError(
            f"max_duration_sec must be <= {MAX_DURATION_CEILING_SEC}"
        )
    row = Policy(
        tenant_id=tenant_id,
        max_duration_sec=md,
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
