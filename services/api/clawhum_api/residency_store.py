"""Per-workspace data residency pin.

Enterprise contracts (especially EU and APAC) require a hard guarantee
that customer data never leaves a named region. This module pins each
tenant to one region. The pin is enforced by ``ResidencyMiddleware``,
which rejects mutating requests when the workspace's region does not
match the region this node was deployed into (``CLAWHUM_REGION``).

Storage follows the same append-only JSONL pattern as quota_store and
ip_allowlist: in-process cache, "latest wins" merge on write. We keep
this independent of FastAPI so the middleware can import it without
circular imports.

Regions
-------

Allowed values are intentionally small and stable so audit, billing and
ingress routing can rely on them:

* ``us``   primary US deployments (default for legacy tenants).
* ``eu``   European Economic Area.
* ``apac`` Asia Pacific.
* ``unset`` no pin: requests are allowed from any region.

``unset`` is the default for any tenant that has never been configured,
so installs without residency policy are unaffected.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

VALID_REGIONS = frozenset({"us", "eu", "apac", "unset"})

_LOCK = Lock()
_CACHE: dict[str, Residency] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class Residency:
    tenant_id: str
    region: str
    enforce: bool
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "region": self.region,
            "enforce": self.enforce,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _default(tenant_id: str) -> Residency:
    return Residency(
        tenant_id=tenant_id,
        region="unset",
        enforce=False,
        updated_at=0.0,
        updated_by="system",
    )


def _path() -> Path:
    return get_settings().residency_path


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _load() -> dict[str, Residency]:
    global _CACHE, _CACHE_PATH
    path = _path()
    if _CACHE is not None and path == _CACHE_PATH:
        return _CACHE
    out: dict[str, Residency] = {}
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
            region = str(rec.get("region") or "unset").lower()
            if region not in VALID_REGIONS:
                region = "unset"
            out[tid] = Residency(
                tenant_id=tid,
                region=region,
                enforce=bool(rec.get("enforce")),
                updated_at=float(rec.get("updated_at") or 0.0),
                updated_by=str(rec.get("updated_by") or "system"),
            )
    _CACHE = out
    _CACHE_PATH = path
    return out


def get(tenant_id: str) -> Residency:
    tid = (tenant_id or "").lower()
    with _LOCK:
        return _load().get(tid) or _default(tid or "anonymous")


def set_(
    *,
    tenant_id: str,
    region: str,
    enforce: bool,
    actor: str,
) -> Residency:
    tid = (tenant_id or "").lower()
    if not tid:
        raise ValueError("tenant_id required")
    region = (region or "unset").lower()
    if region not in VALID_REGIONS:
        raise ValueError(f"unknown region: {region}")
    rec = Residency(
        tenant_id=tid,
        region=region,
        enforce=bool(enforce) and region != "unset",
        updated_at=time.time(),
        updated_by=actor or "unknown",
    )
    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        records = _load()
        records[tid] = rec
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records.values():
                f.write(json.dumps(r.to_dict(), separators=(",", ":"), sort_keys=True))
                f.write("\n")
        tmp.replace(path)
        global _CACHE
        _CACHE = records
    return rec


def list_all() -> list[Residency]:
    with _LOCK:
        return sorted(_load().values(), key=lambda r: r.tenant_id)
