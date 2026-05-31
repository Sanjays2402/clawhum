"""Per-PAT distinct source-IP history for incident response.

When a personal access token leaks, the single ``last_used_ip`` field
already on each PAT is not enough to triage. Operators need to see
*every* IP that has successfully authenticated with the token so they
can answer "was it just my CI box or is there a third party using it
right now?"

This module keeps a bounded per-PAT history of distinct client IPs.
Each row records tenant_id (used for scoping; cross tenant reads are
impossible because the read API filters on the caller's current
tenant), pat_id, ip (resolved client IP, already trust-aware), first
and last seen epoch seconds, the count of successful auths from this
IP, and a truncated user-agent string from the most recent hit.

Storage follows the same append-only JSONL pattern used by
``pat_store`` and ``sso_store`` so multi-tenant deployments do not
require a database. Reads collapse the log into the latest state per
(tenant_id, pat_id, ip) and apply an LRU cap so a token sprayed from
a botnet does not bloat the file unboundedly; the oldest-by-last-seen
entries fall off first.

The write path is best effort: if disk is full or the file is corrupt
for a row, the auth path still succeeds. Reads are tenant-scoped at
the route layer and re-checked here for defense in depth.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings

_MAX_IPS_PER_PAT = 40
_MAX_UA_LEN = 200

_LOCK = Lock()
_CACHE: dict[tuple[str, str], dict[str, "IpHistoryEntry"]] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class IpHistoryEntry:
    """One distinct IP that has authenticated as a given PAT."""

    tenant_id: str
    pat_id: str
    ip: str
    first_seen: float
    last_seen: float
    count: int
    last_ua: str = ""


def reset_cache() -> None:
    """Drop the in-process cache. Used by tests when switching tmp paths."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _trim_ua(ua: str | None) -> str:
    if not ua:
        return ""
    ua = ua.strip()
    if len(ua) <= _MAX_UA_LEN:
        return ua
    return ua[: _MAX_UA_LEN - 1] + "\u2026"


def _path() -> Path:
    override = os.environ.get("CLAWHUM_PAT_IP_HISTORY_PATH")
    if override:
        return Path(override)
    return get_settings().pat_ip_history_path


def _load_locked() -> dict[tuple[str, str], dict[str, IpHistoryEntry]]:
    """Read the JSONL log and collapse to current state."""
    global _CACHE, _CACHE_PATH
    path = _path()
    if _CACHE is not None and _CACHE_PATH == path:
        return _CACHE
    state: dict[tuple[str, str], dict[str, IpHistoryEntry]] = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    tenant_id = str(rec.get("tenant_id") or "")
                    pat_id = str(rec.get("pat_id") or "")
                    ip = str(rec.get("ip") or "")
                    if not tenant_id or not pat_id or not ip:
                        continue
                    bucket = state.setdefault((tenant_id, pat_id), {})
                    if rec.get("deleted"):
                        bucket.pop(ip, None)
                        continue
                    try:
                        bucket[ip] = IpHistoryEntry(
                            tenant_id=tenant_id,
                            pat_id=pat_id,
                            ip=ip,
                            first_seen=float(rec.get("first_seen") or 0.0),
                            last_seen=float(rec.get("last_seen") or 0.0),
                            count=int(rec.get("count") or 0),
                            last_ua=str(rec.get("last_ua") or ""),
                        )
                    except (TypeError, ValueError):
                        continue
        except OSError:
            state = {}
    _CACHE = state
    _CACHE_PATH = path
    return state


def _append(rec: dict[str, Any]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True))
            fh.write("\n")
    except OSError:
        pass


def _rewrite_locked(state: dict[tuple[str, str], dict[str, IpHistoryEntry]]) -> None:
    """Atomically rewrite the log when eviction shrinks live state."""
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                for bucket in state.values():
                    for entry in bucket.values():
                        out.write(
                            json.dumps(
                                {
                                    "tenant_id": entry.tenant_id,
                                    "pat_id": entry.pat_id,
                                    "ip": entry.ip,
                                    "first_seen": entry.first_seen,
                                    "last_seen": entry.last_seen,
                                    "count": entry.count,
                                    "last_ua": entry.last_ua,
                                    "deleted": False,
                                },
                                sort_keys=True,
                            )
                        )
                        out.write("\n")
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError:
        pass


def record(
    *,
    tenant_id: str,
    pat_id: str,
    ip: str,
    user_agent: str | None = None,
    now: float | None = None,
) -> None:
    """Record a successful auth as a (tenant, pat, ip) sighting.

    Best effort. Empty ip or pat_id is silently dropped. When recording
    pushes the per-PAT bucket past the cap, the oldest entry by
    last_seen is evicted and the file is rewritten so it stays bounded.
    """
    ip = (ip or "").strip()
    pat_id = (pat_id or "").strip()
    tenant_id = (tenant_id or "").strip()
    if not ip or not pat_id or not tenant_id:
        return
    ts = float(now if now is not None else time.time())
    ua = _trim_ua(user_agent)
    with _LOCK:
        state = _load_locked()
        bucket = state.setdefault((tenant_id, pat_id), {})
        existing = bucket.get(ip)
        if existing is None:
            entry = IpHistoryEntry(
                tenant_id=tenant_id,
                pat_id=pat_id,
                ip=ip,
                first_seen=ts,
                last_seen=ts,
                count=1,
                last_ua=ua,
            )
        else:
            entry = IpHistoryEntry(
                tenant_id=tenant_id,
                pat_id=pat_id,
                ip=ip,
                first_seen=existing.first_seen or ts,
                last_seen=ts,
                count=existing.count + 1,
                last_ua=ua or existing.last_ua,
            )
        bucket[ip] = entry
        _append(
            {
                "tenant_id": tenant_id,
                "pat_id": pat_id,
                "ip": ip,
                "first_seen": entry.first_seen,
                "last_seen": entry.last_seen,
                "count": entry.count,
                "last_ua": entry.last_ua,
                "deleted": False,
            }
        )
        if len(bucket) > _MAX_IPS_PER_PAT:
            victims = sorted(bucket.values(), key=lambda e: e.last_seen)[
                : len(bucket) - _MAX_IPS_PER_PAT
            ]
            for victim in victims:
                bucket.pop(victim.ip, None)
                _append(
                    {
                        "tenant_id": tenant_id,
                        "pat_id": pat_id,
                        "ip": victim.ip,
                        "deleted": True,
                    }
                )
            _rewrite_locked(state)


def list_for_pat(tenant_id: str, pat_id: str) -> list[IpHistoryEntry]:
    """Return every distinct IP recorded for a PAT, newest last_seen first."""
    with _LOCK:
        state = _load_locked()
        bucket = state.get((tenant_id, pat_id), {})
        rows = [e for e in bucket.values() if e.tenant_id == tenant_id]
    rows.sort(key=lambda e: e.last_seen, reverse=True)
    return rows
