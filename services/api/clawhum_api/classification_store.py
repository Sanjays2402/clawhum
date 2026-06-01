"""Per-workspace data classification policy.

Enterprise procurement and information security teams require every
SaaS vendor to declare how customer data is classified so contractual
handling, retention and access controls can be applied uniformly. This
module pins each tenant to one classification level. The level is:

* ``public``       no confidentiality risk; safe to share publicly.
* ``internal``     default for typical SaaS customers; restricted to
                   the workspace but not subject to special handling.
* ``confidential`` contains business sensitive material; exports are
                   labeled and audited but still self serve.
* ``restricted``   highly sensitive (regulated, PII, IP). Workspace
                   wide bulk exports require an explicit per request
                   acknowledgment header so an admin cannot pull a
                   restricted dataset by accident.

Storage follows the same append only JSONL pattern as
``residency_store`` and ``subprocessors``: in process cache, "latest
wins" merge on write. Kept independent of FastAPI so middleware can
import without circular imports.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

VALID_LEVELS = ("public", "internal", "confidential", "restricted")
_LEVEL_SET = frozenset(VALID_LEVELS)
DEFAULT_LEVEL = "internal"

_LOCK = Lock()
_CACHE: "dict[str, Classification] | None" = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class Classification:
    tenant_id: str
    level: str
    label: str
    handling_contact: str
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "level": self.level,
            "label": self.label,
            "handling_contact": self.handling_contact,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _default(tenant_id: str) -> Classification:
    return Classification(
        tenant_id=tenant_id,
        level=DEFAULT_LEVEL,
        label="",
        handling_contact="",
        updated_at=0.0,
        updated_by="system",
    )


def _path() -> Path:
    return get_settings().classification_path


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _load() -> "dict[str, Classification]":
    global _CACHE, _CACHE_PATH
    path = _path()
    if _CACHE is not None and path == _CACHE_PATH:
        return _CACHE
    out: dict[str, Classification] = {}
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
            level = str(rec.get("level") or DEFAULT_LEVEL).lower()
            if level not in _LEVEL_SET:
                level = DEFAULT_LEVEL
            out[tid] = Classification(
                tenant_id=tid,
                level=level,
                label=str(rec.get("label") or "")[:120],
                handling_contact=str(rec.get("handling_contact") or "")[:200],
                updated_at=float(rec.get("updated_at") or 0.0),
                updated_by=str(rec.get("updated_by") or "system"),
            )
    _CACHE = out
    _CACHE_PATH = path
    return out


def get(tenant_id: str) -> Classification:
    tid = (tenant_id or "").lower()
    with _LOCK:
        return _load().get(tid) or _default(tid or "anonymous")


def set_(
    *,
    tenant_id: str,
    level: str,
    label: str,
    handling_contact: str,
    actor: str,
) -> Classification:
    tid = (tenant_id or "").lower()
    if not tid:
        raise ValueError("tenant_id required")
    lvl = (level or DEFAULT_LEVEL).lower()
    if lvl not in _LEVEL_SET:
        raise ValueError(f"unknown classification level: {level}")
    rec = Classification(
        tenant_id=tid,
        level=lvl,
        label=(label or "").strip()[:120],
        handling_contact=(handling_contact or "").strip()[:200],
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


def list_all() -> "list[Classification]":
    with _LOCK:
        return sorted(_load().values(), key=lambda r: r.tenant_id)


def requires_ack(level: str) -> bool:
    """Whether a workspace at this level must acknowledge bulk exports."""
    return (level or "").lower() == "restricted"
