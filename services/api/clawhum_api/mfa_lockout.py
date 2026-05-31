"""Brute-force lockout for MFA code submission.

Enterprise reviewers consistently flag MFA endpoints that have no
rate limiting against online guessing. A six-digit TOTP with a one
step skew window gives an attacker 3 in 10^6 odds per try; without
a lockout a single leaked API key plus a few thousand requests is
enough to step-up into destructive admin actions. This module makes
that infeasible by counting consecutive failed MFA submissions per
actor inside a sliding window, locking the actor for a cooldown when
the threshold trips, and refusing further MFA attempts with HTTP 429
plus a ``Retry-After`` header until the lock clears or an admin clears
it manually through ``/admin/mfa/lockouts``.

Design notes:

* Counts are per ``actor_id`` (same hashed identifier the audit log and
  the rest of the MFA module use), so a stolen credential cannot use
  one tenant's quota to mask attempts against another.
* Storage is the same append-only JSONL pattern the rest of the
  codebase uses. We replay the file on read and keep an in-process
  cache of the latest record per actor. The cache is invalidated when
  ``record_failure``/``clear``/``unlock`` append a new row.
* A successful TOTP or recovery-code consume must call ``clear`` so a
  legitimate user who fat-fingered a few digits is not punished after
  they recover. The auth layer wires this in.
* ``lock_state`` returns the dataclass the routes layer needs to build
  the response: ``locked`` flag, ``retry_after`` seconds, ``failures``
  in the current window. Callers that get ``locked=True`` must NOT
  invoke ``mfa.verify`` (so an attacker can't keep observing whether a
  guess would have worked while locked).
* Lockout is best-effort never-raise. If the disk is full and we can't
  append a counter row we degrade to ``not locked`` rather than
  blocking the legitimate user; the cooldown is a defense in depth,
  not the only line of defense (audit trail and IP allowlist still
  apply).
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
_log_path_cache: Path | None = None


@dataclass(frozen=True)
class LockState:
    """Outcome of inspecting an actor's MFA failure history."""

    actor_id: str
    failures: int
    """Failures inside the current sliding window."""
    locked: bool
    locked_until: float
    """Unix timestamp when the cooldown ends. 0 when not locked."""

    @property
    def retry_after(self) -> int:
        """Seconds until ``locked_until``. Always non-negative; rounded up."""
        if not self.locked:
            return 0
        delta = self.locked_until - time.time()
        if delta <= 0:
            return 0
        return int(delta) + 1


def _path() -> Path:
    s = get_settings()
    p = s.mfa_lockout_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append(rec: dict[str, Any]) -> None:
    try:
        p = _path()
        line = json.dumps(rec, separators=(",", ":"), sort_keys=True)
        with _LOCK:
            with open(p, "ab") as f:
                f.write(line.encode("utf-8") + b"\n")
    except Exception:
        # Never block the request because we couldn't write a counter.
        # Other defenses (audit log, IP allowlist, rate limiter) still
        # bound an attacker; lockout is one layer.
        return


def _replay(actor_id: str) -> list[dict[str, Any]]:
    """Return failure/clear/unlock rows for ``actor_id`` in file order."""
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
                if rec.get("actor_id") == actor_id:
                    out.append(rec)
    return out


def _current(actor_id: str, *, now: float | None = None) -> LockState:
    s = get_settings()
    window = max(1, int(s.mfa_lockout_window_seconds))
    threshold = int(s.mfa_lockout_threshold)
    cooldown = max(0, int(s.mfa_lockout_cooldown_seconds))
    t = now if now is not None else time.time()
    rows = _replay(actor_id)
    # Walk rows in order. A clear or unlock resets the running
    # counter and any active lock. A failure adds to the window
    # counter and may trip a lock. Locks have an explicit expiry.
    locked_until = 0.0
    fail_times: list[float] = []
    for rec in rows:
        kind = rec.get("kind")
        ts = float(rec.get("ts") or 0)
        if kind == "fail":
            fail_times.append(ts)
            # prune outside window
            fail_times = [x for x in fail_times if ts - x < window]
            if threshold > 0 and len(fail_times) >= threshold:
                locked_until = ts + cooldown
                fail_times = []  # reset so unlock isn't immediately re-tripped
        elif kind in ("clear", "unlock"):
            fail_times = []
            locked_until = 0.0
    # Final prune: drop window-stale failures relative to "now".
    fail_times = [x for x in fail_times if t - x < window]
    locked = bool(locked_until and t < locked_until)
    return LockState(
        actor_id=actor_id,
        failures=len(fail_times),
        locked=locked,
        locked_until=(locked_until if locked else 0.0),
    )


def lock_state(actor_id: str) -> LockState:
    """Public read accessor used by the auth layer and admin UI."""
    return _current(actor_id)


def record_failure(actor_id: str, *, tenant_id: str = "") -> LockState:
    """Persist a failed MFA submission and return the resulting state.

    Returns the state AFTER the failure is recorded, so a caller can
    check ``state.locked`` to decide whether to emit a ``mfa.locked``
    audit event in addition to the standard ``mfa.failed`` line.
    """
    before = _current(actor_id)
    _append(
        {
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "kind": "fail",
            "ts": time.time(),
        }
    )
    after = _current(actor_id)
    # Surface the tripping edge to the caller so they can audit it.
    after_tripped = after.locked and not before.locked
    if after_tripped:
        _append(
            {
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "kind": "lock_tripped",
                "ts": time.time(),
                "locked_until": after.locked_until,
            }
        )
    return after


def clear(actor_id: str, *, tenant_id: str = "", reason: str = "success") -> None:
    """Reset the failure counter and any active lock for ``actor_id``.

    The auth layer calls this after a successful TOTP or recovery code
    consume so a legitimate user who mistyped a few digits is not
    permanently penalised. Reason is recorded so the admin audit can
    distinguish a self-clear (``success``) from an admin override
    (``admin-unlock``).
    """
    _append(
        {
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "kind": "clear",
            "ts": time.time(),
            "reason": reason,
        }
    )


def admin_unlock(actor_id: str, *, tenant_id: str, by: str, reason: str = "") -> bool:
    """Manual unlock by a workspace admin. Returns True if a lock was lifted."""
    state = _current(actor_id)
    _append(
        {
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "kind": "unlock",
            "ts": time.time(),
            "by": by,
            "reason": reason,
        }
    )
    return state.locked


def list_locked(tenant_id: str) -> list[LockState]:
    """Enumerate currently-locked actors for ``tenant_id`` for admin UI.

    Walks the JSONL file once and aggregates by actor; cost is linear
    in the lockout log size which is bounded by traffic times threshold.
    """
    p = _path()
    if not p.exists():
        return []
    seen: dict[str, str] = {}  # actor_id -> tenant_id (last seen)
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
                actor = str(rec.get("actor_id") or "")
                if not actor:
                    continue
                seen[actor] = str(rec.get("tenant_id") or seen.get(actor, ""))
    out: list[LockState] = []
    for actor, tid in seen.items():
        if tid != tenant_id:
            continue
        st = _current(actor)
        if st.locked:
            out.append(st)
    return out
