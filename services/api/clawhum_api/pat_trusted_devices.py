"""Per-PAT trusted-device approval list.

When a PAT has ``require_device_approval`` turned on, the auth path
computes a stable device fingerprint from the resolved client IP
(first /24 for IPv4, first /48 for IPv6) and a coarse User-Agent
family (browser/library token). Requests from a fingerprint that has
not been pre-approved are rejected with HTTP 403 and the unknown
device is recorded as ``pending`` so the workspace owner can review
and approve it from /settings/keys.

The first device ever seen on a PAT immediately after the owner
flips approval on is NOT auto-trusted: the owner must take an
explicit action to approve it. This avoids the foot-gun where a
leaked-and-already-in-use token would have the attacker's device
auto-trusted the instant strict mode is enabled.

Storage follows the same append-only JSONL reduction pattern used by
``pat_store`` and ``pat_ip_history`` so multi-tenant deployments do
not need a database. Reads collapse the log into the latest state
per (tenant_id, pat_id, fingerprint) and apply an LRU cap so a token
sprayed from a botnet cannot bloat the file unboundedly.

Every read is tenant-scoped at the route layer AND re-checked here
for defense in depth: a probing attacker who learns a PAT id from
another workspace gets a 404 from the route and an empty list from
this module.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from clawhum_core.settings import get_settings

# Cap distinct devices we remember per PAT. When exceeded, the
# oldest-by-last-seen entries fall off. 64 is generous for a real
# human (laptop + phone + CI + a handful of dev boxes) and small
# enough that a hostile spray cannot DoS the JSONL file.
_MAX_DEVICES_PER_PAT = 64
_MAX_UA_LEN = 200
_MAX_LABEL_LEN = 80

_LOCK = Lock()
_CACHE: dict[tuple[str, str], dict[str, "TrustedDevice"]] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class TrustedDevice:
    """One device fingerprint that has tried to use this PAT."""

    tenant_id: str
    pat_id: str
    fingerprint: str  # 16 hex chars; sha256(ip_prefix + ua_family)[:16]
    status: str  # "approved" or "pending"
    label: str
    first_seen: float
    last_seen: float
    count: int
    last_ua: str
    last_ip: str


def reset_cache() -> None:
    """Drop the in-process cache. Used by tests when switching tmp paths."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _trim(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "\u2026"


def _path() -> Path:
    override = os.environ.get("CLAWHUM_PAT_TRUSTED_DEVICES_PATH")
    if override:
        p = Path(override)
    else:
        # Co-locate with the PAT store by default so backups capture
        # both together.
        p = get_settings().pat_path.parent / "pat_trusted_devices.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------

# Coarse User-Agent family classifier. Two requests from "the same
# browser on the same network" should produce the same fingerprint
# even when the patch version of the browser updates. We extract the
# major product token (e.g. "Chrome/118" -> "chrome"; "curl/8.4.0" ->
# "curl"; "python-requests/2.31.0" -> "python-requests"). Unknown UAs
# collapse to "other" so a hostile client cannot generate unbounded
# distinct fingerprints by mutating the UA string alone.
_UA_FAMILIES = [
    ("chrome", re.compile(r"\bchrome/", re.IGNORECASE)),
    ("safari", re.compile(r"\bsafari/", re.IGNORECASE)),
    ("firefox", re.compile(r"\bfirefox/", re.IGNORECASE)),
    ("edge", re.compile(r"\bedg/", re.IGNORECASE)),
    ("curl", re.compile(r"\bcurl/", re.IGNORECASE)),
    ("wget", re.compile(r"\bwget/", re.IGNORECASE)),
    ("httpie", re.compile(r"\bhttpie/", re.IGNORECASE)),
    ("python-requests", re.compile(r"\bpython-requests/", re.IGNORECASE)),
    ("python-httpx", re.compile(r"\bpython-httpx/", re.IGNORECASE)),
    ("python-urllib", re.compile(r"\bpython-urllib", re.IGNORECASE)),
    ("go-http-client", re.compile(r"\bgo-http-client/", re.IGNORECASE)),
    ("node-fetch", re.compile(r"\bnode-fetch/", re.IGNORECASE)),
    ("axios", re.compile(r"\baxios/", re.IGNORECASE)),
    ("postman", re.compile(r"\bpostmanruntime/", re.IGNORECASE)),
]


def _ua_family(ua: str) -> str:
    if not ua:
        return "none"
    for name, pat in _UA_FAMILIES:
        if pat.search(ua):
            return name
    return "other"


def _ip_prefix(ip: str) -> str:
    """Coarse network prefix: /24 for IPv4, /48 for IPv6.

    We intentionally drop the host bits so a laptop with a dynamic
    DHCP lease on the same office network keeps the same fingerprint
    across reconnects. An attacker on a different network gets a
    different fingerprint and is forced through the approval flow.
    Empty / invalid input returns "unknown" so the device shows up
    explicitly in the pending list instead of silently merging with
    every other peer that lacks an IP.
    """
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv4Address):
        net = ipaddress.ip_network(f"{addr}/24", strict=False)
    else:
        net = ipaddress.ip_network(f"{addr}/48", strict=False)
    return str(net.network_address)


def compute_fingerprint(ip: str, user_agent: str) -> str:
    """Stable 16-hex-char fingerprint for (ip_prefix, ua_family)."""
    payload = f"v1|{_ip_prefix(ip)}|{_ua_family(user_agent)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _iter_records() -> Iterable[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _append(rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    p = _path()
    with _LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        _invalidate_locked()


def _invalidate_locked() -> None:
    global _CACHE, _CACHE_PATH
    _CACHE = None
    _CACHE_PATH = None


def _reduce() -> dict[tuple[str, str], dict[str, TrustedDevice]]:
    """Walk the log and collapse to latest state per fingerprint."""
    global _CACHE, _CACHE_PATH
    cur = _path()
    with _LOCK:
        if _CACHE is not None and _CACHE_PATH == cur:
            return _CACHE
    out: dict[tuple[str, str], dict[str, TrustedDevice]] = {}
    for rec in _iter_records():
        try:
            tenant = str(rec["tenant_id"])
            pat_id = str(rec["pat_id"])
            fp = str(rec["fingerprint"])
        except (KeyError, TypeError):
            continue
        if rec.get("deleted"):
            bucket = out.get((tenant, pat_id))
            if bucket is not None:
                bucket.pop(fp, None)
            continue
        dev = TrustedDevice(
            tenant_id=tenant,
            pat_id=pat_id,
            fingerprint=fp,
            status=str(rec.get("status", "pending")),
            label=_trim(str(rec.get("label", "")), _MAX_LABEL_LEN),
            first_seen=float(rec.get("first_seen", 0.0) or 0.0),
            last_seen=float(rec.get("last_seen", 0.0) or 0.0),
            count=int(rec.get("count", 0) or 0),
            last_ua=_trim(str(rec.get("last_ua", "") or ""), _MAX_UA_LEN),
            last_ip=str(rec.get("last_ip", "") or ""),
        )
        out.setdefault((tenant, pat_id), {})[fp] = dev
    # Apply per-PAT LRU cap. Approved devices are protected; only
    # pending entries are evicted when the bucket overflows so an
    # attacker spraying junk fingerprints cannot push the owner's
    # real approved devices out of the list.
    for key, bucket in out.items():
        if len(bucket) <= _MAX_DEVICES_PER_PAT:
            continue
        overflow = len(bucket) - _MAX_DEVICES_PER_PAT
        pendings = [d for d in bucket.values() if d.status != "approved"]
        pendings.sort(key=lambda d: d.last_seen)
        for d in pendings[:overflow]:
            bucket.pop(d.fingerprint, None)
    with _LOCK:
        _CACHE = out
        _CACHE_PATH = cur
    return out


def list_for_pat(tenant_id: str, pat_id: str) -> list[TrustedDevice]:
    """Tenant-scoped list of devices for a PAT, newest last_seen first."""
    bucket = _reduce().get((tenant_id, pat_id), {})
    items = list(bucket.values())
    items.sort(key=lambda d: (d.status != "approved", -d.last_seen))
    return items


def get_device(
    tenant_id: str, pat_id: str, fingerprint: str
) -> TrustedDevice | None:
    bucket = _reduce().get((tenant_id, pat_id), {})
    return bucket.get(fingerprint)


def has_approved_device(tenant_id: str, pat_id: str) -> bool:
    bucket = _reduce().get((tenant_id, pat_id), {})
    return any(d.status == "approved" for d in bucket.values())


def is_approved(
    tenant_id: str, pat_id: str, fingerprint: str
) -> bool:
    d = get_device(tenant_id, pat_id, fingerprint)
    return d is not None and d.status == "approved"


def record_pending(
    *,
    tenant_id: str,
    pat_id: str,
    fingerprint: str,
    ip: str,
    user_agent: str,
    now: float | None = None,
) -> TrustedDevice:
    """Insert or refresh a pending device sighting.

    Existing approved devices are left untouched (this path is only
    called when the auth layer has already determined the device is
    NOT approved). Existing pending entries have their last_seen and
    count bumped so the owner sees how aggressive the unknown caller
    is.
    """
    now = now if now is not None else time.time()
    existing = get_device(tenant_id, pat_id, fingerprint)
    if existing is not None and existing.status == "approved":
        return existing
    first_seen = existing.first_seen if existing else now
    count = (existing.count if existing else 0) + 1
    rec = {
        "tenant_id": tenant_id,
        "pat_id": pat_id,
        "fingerprint": fingerprint,
        "status": "pending",
        "label": existing.label if existing else "",
        "first_seen": first_seen,
        "last_seen": now,
        "count": count,
        "last_ua": _trim(user_agent, _MAX_UA_LEN),
        "last_ip": ip or "",
    }
    _append(rec)
    return TrustedDevice(**rec)


def touch_approved(
    *,
    tenant_id: str,
    pat_id: str,
    fingerprint: str,
    ip: str,
    user_agent: str,
    now: float | None = None,
) -> None:
    """Refresh last_seen / count on an already-approved device."""
    now = now if now is not None else time.time()
    existing = get_device(tenant_id, pat_id, fingerprint)
    if existing is None or existing.status != "approved":
        return
    rec = {
        "tenant_id": tenant_id,
        "pat_id": pat_id,
        "fingerprint": fingerprint,
        "status": "approved",
        "label": existing.label,
        "first_seen": existing.first_seen,
        "last_seen": now,
        "count": existing.count + 1,
        "last_ua": _trim(user_agent, _MAX_UA_LEN),
        "last_ip": ip or existing.last_ip,
    }
    _append(rec)


def approve(
    *,
    tenant_id: str,
    pat_id: str,
    fingerprint: str,
    label: str = "",
    now: float | None = None,
) -> TrustedDevice | None:
    """Promote a device to approved. Returns the new state or None."""
    now = now if now is not None else time.time()
    existing = get_device(tenant_id, pat_id, fingerprint)
    if existing is None:
        return None
    rec = {
        "tenant_id": tenant_id,
        "pat_id": pat_id,
        "fingerprint": fingerprint,
        "status": "approved",
        "label": _trim(label or existing.label, _MAX_LABEL_LEN),
        "first_seen": existing.first_seen or now,
        "last_seen": existing.last_seen or now,
        "count": existing.count,
        "last_ua": existing.last_ua,
        "last_ip": existing.last_ip,
    }
    _append(rec)
    return TrustedDevice(**rec)


def revoke(
    *,
    tenant_id: str,
    pat_id: str,
    fingerprint: str,
) -> bool:
    """Remove a device (approved or pending). Returns True if it existed."""
    existing = get_device(tenant_id, pat_id, fingerprint)
    if existing is None:
        return False
    rec = {
        "tenant_id": tenant_id,
        "pat_id": pat_id,
        "fingerprint": fingerprint,
        "deleted": True,
    }
    _append(rec)
    return True


def revoke_all_for_pat(*, tenant_id: str, pat_id: str) -> int:
    """Erase every device row for a PAT. Returns the count removed."""
    bucket = list(_reduce().get((tenant_id, pat_id), {}).keys())
    for fp in bucket:
        revoke(tenant_id=tenant_id, pat_id=pat_id, fingerprint=fp)
    return len(bucket)
