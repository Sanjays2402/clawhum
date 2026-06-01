"""Brute-force lockout for personal-access-token authentication attempts.

Why this exists
---------------
Enterprise security reviews (SOC2 CC6.7, ISO 27001 A.9.4.2) require
that an attacker cannot guess credentials online without consequence.
The MFA submission path already has per-actor lockout
(``mfa_lockout``) but the front door, the ``X-API-Key`` header itself,
had nothing: a script could submit thousands of random
``pat_xxxxxxxx`` values per second, each returning ``401 invalid api
key`` with no rate limit and no record visible to the workspace
admin. ``pat_xxxxxxxx`` secrets are 24-byte url-safe tokens so the
search space is enormous, but auditors flag the missing control on
principle and a misconfigured proxy could amplify the risk.

This module counts failed ``pat_``-prefixed auth attempts per source
IP inside a sliding window. When the threshold trips, subsequent PAT
auth attempts from that IP (regardless of which workspace they
target, because the workspace is unknown until the token validates)
are short-circuited with ``HTTP 429 Too Many Requests`` plus a
``Retry-After`` header. A successful PAT auth from the same IP clears
the counter so a legitimate user who typed the wrong secret twice is
not punished after they paste the right one. A workspace admin can
inspect or force-unlock IPs through ``/admin/pat-auth-lockout``
(MFA-gated).

The counter store is global (per IP, not per tenant) because the
attacker does not know the tenant at the point of attack. The admin
UI is tenant-scoped: each workspace can see locks it cares about,
clear them, and audit who cleared what.

Design choices that mirror the rest of the codebase:

* JSONL append-only log with last-event-wins replay, identical shape
  to ``mfa_lockout`` so operators only learn one storage pattern.
* Never-raise on write: a full disk degrades to "not locked" rather
  than blocking the authenticated user. Other defenses (audit log,
  IP allowlist, PAT scopes, body-size cap) still apply.
* The IP is resolved through the existing trusted-proxy aware
  ``ip_allowlist.client_ip_from_request`` helper so an X-Forwarded-For
  spoofer cannot trivially launder the brute-force across IPs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings


_LOCK = Lock()
_PATH_CACHE: Path | None = None


@dataclass(frozen=True)
class LockState:
    """Outcome of inspecting one source IP's PAT failure history."""

    ip: str
    failures: int
    locked: bool
    locked_until: float
    last_tenant_id: str = ""
    affiliated_tenants: tuple[str, ...] = ()

    @property
    def retry_after(self) -> int:
        if not self.locked:
            return 0
        delta = self.locked_until - time.time()
        if delta <= 0:
            return 0
        return int(delta) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "failures": self.failures,
            "locked": self.locked,
            "locked_until": self.locked_until,
            "retry_after": self.retry_after,
            "last_tenant_id": self.last_tenant_id,
            "affiliated_tenants": list(self.affiliated_tenants),
        }


def _path() -> Path:
    global _PATH_CACHE
    p = Path(get_settings().pat_auth_lockout_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _PATH_CACHE = p
    return p


def _append(rec: dict[str, Any]) -> None:
    try:
        line = json.dumps(rec, separators=(",", ":"), sort_keys=True)
        with _LOCK:
            with open(_path(), "ab") as f:
                f.write(line.encode("utf-8") + b"\n")
    except Exception:
        # Never block auth because we couldn't write a counter row.
        return


def _replay(ip: str) -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with _LOCK:
        with open(p, "rb") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                if rec.get("ip") == ip:
                    out.append(rec)
    return out


def _current(ip: str, *, now: float | None = None) -> LockState:
    s = get_settings()
    window = max(1, int(s.pat_auth_lockout_window_seconds))
    threshold = int(s.pat_auth_lockout_threshold)
    cooldown = max(0, int(s.pat_auth_lockout_cooldown_seconds))
    t = now if now is not None else time.time()
    rows = _replay(ip)
    locked_until = 0.0
    fail_times: list[float] = []
    last_tenant = ""
    affiliated: set[str] = set()
    for rec in rows:
        kind = rec.get("kind")
        ts = float(rec.get("ts") or 0)
        tid = str(rec.get("tenant_id") or "")
        if tid:
            last_tenant = tid
            affiliated.add(tid)
        if kind == "fail":
            fail_times.append(ts)
            fail_times = [x for x in fail_times if ts - x < window]
            if threshold > 0 and len(fail_times) >= threshold:
                locked_until = ts + cooldown
                fail_times = []
        elif kind in ("clear", "unlock"):
            fail_times = []
            locked_until = 0.0
    fail_times = [x for x in fail_times if t - x < window]
    locked = bool(locked_until and t < locked_until)
    return LockState(
        ip=ip,
        failures=len(fail_times),
        locked=locked,
        locked_until=(locked_until if locked else 0.0),
        last_tenant_id=last_tenant,
        affiliated_tenants=tuple(sorted(affiliated)),
    )


def lock_state(ip: str) -> LockState:
    if not ip:
        return LockState(ip="", failures=0, locked=False, locked_until=0.0)
    return _current(ip)


def record_failure(ip: str, *, tenant_id: str = "") -> LockState:
    """Persist a failed PAT auth attempt for ``ip`` and return new state."""
    if not ip:
        return LockState(ip="", failures=0, locked=False, locked_until=0.0)
    before = _current(ip)
    _append(
        {
            "ip": ip,
            "tenant_id": tenant_id or "",
            "kind": "fail",
            "ts": time.time(),
        }
    )
    after = _current(ip)
    if after.locked and not before.locked:
        _append(
            {
                "ip": ip,
                "tenant_id": tenant_id or "",
                "kind": "lock_tripped",
                "ts": time.time(),
                "locked_until": after.locked_until,
            }
        )
    return after


def clear(ip: str, *, tenant_id: str = "", reason: str = "success") -> None:
    """Reset the failure counter for ``ip``. Called on a successful PAT auth."""
    if not ip:
        return
    _append(
        {
            "ip": ip,
            "tenant_id": tenant_id or "",
            "kind": "clear",
            "ts": time.time(),
            "reason": reason,
        }
    )


def admin_unlock(ip: str, *, tenant_id: str, by: str, reason: str = "") -> bool:
    """Manual unlock by a workspace admin. Returns True if a lock was lifted."""
    state = _current(ip)
    _append(
        {
            "ip": ip,
            "tenant_id": tenant_id,
            "kind": "unlock",
            "ts": time.time(),
            "by": by,
            "reason": reason,
        }
    )
    return state.locked


def tag_tenant(ip: str, *, tenant_id: str) -> None:
    """Record that ``ip`` has been seen authenticating against ``tenant_id``.

    Used by the workspace-key auth path to associate a source IP
    with a workspace so that any subsequent PAT brute-force lock
    from that IP is visible to that workspace's admin overview
    (without leaking it to unrelated workspaces). Best-effort: the
    file is append-only JSONL and never blocks the auth response.
    """
    if not ip or not tenant_id:
        return
    _append(
        {
            "ip": ip,
            "tenant_id": tenant_id,
            "kind": "tag",
            "ts": time.time(),
        }
    )


def list_locked(tenant_id: str = "") -> list[LockState]:
    """Enumerate currently-locked IPs.

    When ``tenant_id`` is empty, returns every active lock (admin
    bootstrap path). When set, only locks whose most recent failure
    was associated with that tenant are returned, so a workspace
    admin sees attacks against tokens they actually mint.
    """
    p = _path()
    if not p.exists():
        return []
    seen_ips: list[str] = []
    seen_set: set[str] = set()
    with _LOCK:
        with open(p, "rb") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                ip = str(rec.get("ip") or "")
                if not ip or ip in seen_set:
                    continue
                seen_set.add(ip)
                seen_ips.append(ip)
    out: list[LockState] = []
    for ip in seen_ips:
        st = _current(ip)
        if not st.locked:
            continue
        if tenant_id:
            # Hide locks that belong to a different workspace.
            if st.last_tenant_id and st.last_tenant_id != tenant_id:
                continue
            # Show the lock to this workspace when it is either
            # already tagged with this tenant or has touched this
            # workspace at least once (affinity). Unknown-tenant
            # locks with no affinity stay invisible per workspace
            # so cross-tenant admins do not see every probe in the
            # deployment.
            if not (
                st.last_tenant_id == tenant_id
                or tenant_id in st.affiliated_tenants
            ):
                continue
        out.append(st)
    out.sort(key=lambda s: s.locked_until, reverse=True)
    return out
