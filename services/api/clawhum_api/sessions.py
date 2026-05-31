"""Active session tracking and force-logout.

Why this module exists
----------------------
Every other store in this repo tracks long-lived credentials (API
keys, PATs, SCIM tokens). Enterprise buyers also need a record of
the live, short-lived authenticated touches those credentials make,
so that:

1. Workspace owners can see which actors are actively hitting the
   API right now, from which IP and user agent.
2. An owner who suspects a credential is being misused can force
   logout (revoke every active session) for that actor in one click,
   without having to revoke the underlying key.
3. The workspace can pin an idle session timeout and an absolute
   session lifetime so a leaked credential window is bounded even
   when the key itself is still valid. A separate cap bounds the
   maximum TTL of any new personal access token minted in the
   workspace.

A "session" here is the (tenant_id, actor, ip, ua_hash) tuple. It is
created on the first authenticated request for that tuple and the
``last_seen`` timestamp is bumped on every subsequent request. This
mirrors how every consumer SaaS surfaces "active devices" without
inventing a stateful sign-in flow on top of the existing API-key
auth.

Storage follows the same append-only JSONL pattern used by every
other tenant-scoped store in this service. The in-process index is
last-writer-wins per session id. A best-effort write of one line per
request is fine for the small per-tenant cardinality this feature
targets; multi-replica deployments should swap the store for Redis
the same way the rate limiter would.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 16
SESSION_PREFIX = "sess_"

# In-process caches, keyed by the on-disk path so test fixtures that
# point a temporary settings object at a different file invalidate
# cleanly without poking module globals.
_SESSIONS_CACHE: dict[str, dict[str, "Session"]] | None = None
_SESSIONS_CACHE_PATH: Path | None = None
_POLICY_CACHE: dict[str, "SessionPolicy"] | None = None
_POLICY_CACHE_PATH: Path | None = None


def _new_id() -> str:
    return SESSION_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _ua_hash(ua: str) -> str:
    # Short, deterministic, no PII. 12 hex chars is enough cardinality
    # for the "is this the same browser" question we are answering.
    return hashlib.sha256((ua or "").encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Session:
    id: str
    tenant_id: str
    actor: str                 # e.g. "writer-token" or "pat:my-laptop"
    actor_kind: str            # "key" | "pat" | "dev"
    ip: str
    ua_hash: str
    ua_label: str              # short, human-readable user-agent excerpt
    first_seen: float
    last_seen: float
    request_count: int
    revoked: bool = False
    revoked_at: float = 0.0
    revoke_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SessionPolicy:
    tenant_id: str
    # 0 means "no idle timeout"; positive integer minutes.
    idle_timeout_minutes: int = 0
    # 0 means "no absolute cap"; positive integer minutes from first_seen.
    absolute_max_minutes: int = 0
    # 0 means "use the global pat_max_ttl_days"; positive integer is a
    # tighter cap measured in minutes that overrides the global default
    # when minting new PATs in this workspace.
    max_pat_lifetime_minutes: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _sessions_path() -> Path:
    return Path(get_settings().sessions_path)


def _policy_path() -> Path:
    return Path(get_settings().session_policy_path)


# ---------------------------------------------------------------- session io

def _load_sessions_locked() -> dict[str, dict[str, Session]]:
    global _SESSIONS_CACHE, _SESSIONS_CACHE_PATH
    p = _sessions_path()
    if _SESSIONS_CACHE is not None and _SESSIONS_CACHE_PATH == p:
        return _SESSIONS_CACHE
    out: dict[str, dict[str, Session]] = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = rec.get("id")
                tenant = rec.get("tenant_id")
                if not sid or not tenant:
                    continue
                sess = Session(
                    id=sid,
                    tenant_id=tenant,
                    actor=rec.get("actor", ""),
                    actor_kind=rec.get("actor_kind", "key"),
                    ip=rec.get("ip", ""),
                    ua_hash=rec.get("ua_hash", ""),
                    ua_label=rec.get("ua_label", ""),
                    first_seen=float(rec.get("first_seen", 0.0) or 0.0),
                    last_seen=float(rec.get("last_seen", 0.0) or 0.0),
                    request_count=int(rec.get("request_count", 0) or 0),
                    revoked=bool(rec.get("revoked", False)),
                    revoked_at=float(rec.get("revoked_at", 0.0) or 0.0),
                    revoke_reason=str(rec.get("revoke_reason", "")),
                )
                out.setdefault(tenant, {})[sid] = sess
    _SESSIONS_CACHE = out
    _SESSIONS_CACHE_PATH = p
    return out


def _append_session_locked(sess: Session) -> None:
    p = _sessions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sess.to_dict()) + "\n")


# ---------------------------------------------------------------- policy io

def _load_policies_locked() -> dict[str, SessionPolicy]:
    global _POLICY_CACHE, _POLICY_CACHE_PATH
    p = _policy_path()
    if _POLICY_CACHE is not None and _POLICY_CACHE_PATH == p:
        return _POLICY_CACHE
    out: dict[str, SessionPolicy] = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tenant = rec.get("tenant_id")
                if not tenant:
                    continue
                out[tenant] = SessionPolicy(
                    tenant_id=tenant,
                    idle_timeout_minutes=max(0, int(rec.get("idle_timeout_minutes", 0) or 0)),
                    absolute_max_minutes=max(0, int(rec.get("absolute_max_minutes", 0) or 0)),
                    max_pat_lifetime_minutes=max(0, int(rec.get("max_pat_lifetime_minutes", 0) or 0)),
                    updated_at=float(rec.get("updated_at", 0.0) or 0.0),
                )
    _POLICY_CACHE = out
    _POLICY_CACHE_PATH = p
    return out


def _append_policy_locked(policy: SessionPolicy) -> None:
    p = _policy_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(policy.to_dict()) + "\n")


# ---------------------------------------------------------------- public api


def get_policy(tenant_id: str) -> SessionPolicy:
    """Return the active policy for ``tenant_id`` (defaults are 0 = unlimited)."""
    with _LOCK:
        policies = _load_policies_locked()
        return policies.get(tenant_id) or SessionPolicy(tenant_id=tenant_id)


def set_policy(
    tenant_id: str,
    *,
    idle_timeout_minutes: int,
    absolute_max_minutes: int,
    max_pat_lifetime_minutes: int,
) -> SessionPolicy:
    if not tenant_id:
        raise ValueError("tenant_id required")
    # Reject obviously hostile values; 5-year cap matches enterprise
    # certificate hygiene and keeps the integer math defensible.
    cap = 60 * 24 * 365 * 5
    idle = max(0, min(int(idle_timeout_minutes or 0), cap))
    absolute = max(0, min(int(absolute_max_minutes or 0), cap))
    pat_cap = max(0, min(int(max_pat_lifetime_minutes or 0), cap))
    if absolute and idle and idle > absolute:
        raise ValueError("idle_timeout_minutes cannot exceed absolute_max_minutes")
    policy = SessionPolicy(
        tenant_id=tenant_id,
        idle_timeout_minutes=idle,
        absolute_max_minutes=absolute,
        max_pat_lifetime_minutes=pat_cap,
        updated_at=time.time(),
    )
    with _LOCK:
        _append_policy_locked(policy)
        policies = _load_policies_locked()
        policies[tenant_id] = policy
    return policy


def list_sessions(tenant_id: str, *, include_revoked: bool = False) -> list[Session]:
    """Return sessions for ``tenant_id`` ordered by recency."""
    with _LOCK:
        store = _load_sessions_locked().get(tenant_id, {})
        out = list(store.values())
    if not include_revoked:
        out = [s for s in out if not s.revoked]
    out.sort(key=lambda s: s.last_seen, reverse=True)
    return out


def get_session(tenant_id: str, session_id: str) -> Session | None:
    with _LOCK:
        return _load_sessions_locked().get(tenant_id, {}).get(session_id)


def _truncate_ua(ua: str) -> str:
    if not ua:
        return "unknown"
    return ua[:80]


def touch(
    *,
    tenant_id: str,
    actor: str,
    actor_kind: str,
    ip: str,
    user_agent: str,
    now: float | None = None,
) -> Session:
    """Create or refresh a session row for this authenticated request.

    Returns the up-to-date Session. Idempotent: subsequent calls with
    the same (tenant, actor, ip, ua) bump ``last_seen`` and
    ``request_count`` without spawning new rows. If a matching row
    exists but is revoked, it is returned unchanged so the auth layer
    can reject the request instead of silently resurrecting a session.
    """
    if not tenant_id or not actor:
        raise ValueError("tenant_id and actor required")
    now = time.time() if now is None else now
    ua_hash = _ua_hash(user_agent)
    ua_label = _truncate_ua(user_agent)
    with _LOCK:
        store = _load_sessions_locked()
        tenant_store = store.setdefault(tenant_id, {})
        match = next(
            (
                s for s in tenant_store.values()
                if s.actor == actor and s.ip == ip and s.ua_hash == ua_hash
            ),
            None,
        )
        if match is not None and match.revoked:
            # Do not silently resurrect a revoked session for the same
            # tuple. Returning the revoked row lets the auth layer
            # reject the request; the credential holder has to obtain
            # a fresh credential or have an owner clear the revoke.
            return match
        if match is None:
            sess = Session(
                id=_new_id(),
                tenant_id=tenant_id,
                actor=actor,
                actor_kind=actor_kind,
                ip=ip,
                ua_hash=ua_hash,
                ua_label=ua_label,
                first_seen=now,
                last_seen=now,
                request_count=1,
            )
        else:
            sess = replace(
                match,
                last_seen=now,
                request_count=match.request_count + 1,
                ua_label=ua_label,
            )
        tenant_store[sess.id] = sess
        _append_session_locked(sess)
    return sess


def is_expired(sess: Session, policy: SessionPolicy, now: float | None = None) -> tuple[bool, str]:
    """Return (expired?, reason) given the policy.

    Reason is one of "idle", "absolute", or "" when not expired.
    """
    now = time.time() if now is None else now
    if policy.idle_timeout_minutes > 0:
        if now - sess.last_seen > policy.idle_timeout_minutes * 60:
            return True, "idle"
    if policy.absolute_max_minutes > 0:
        if now - sess.first_seen > policy.absolute_max_minutes * 60:
            return True, "absolute"
    return False, ""


def revoke(tenant_id: str, session_id: str, *, reason: str = "") -> Session | None:
    """Revoke a single session. Returns the updated row or None if missing."""
    now = time.time()
    with _LOCK:
        store = _load_sessions_locked()
        tenant_store = store.get(tenant_id, {})
        existing = tenant_store.get(session_id)
        if existing is None:
            return None
        if existing.revoked:
            return existing
        updated = replace(existing, revoked=True, revoked_at=now, revoke_reason=reason[:120])
        tenant_store[session_id] = updated
        _append_session_locked(updated)
    return updated


def revoke_all_for_actor(tenant_id: str, actor: str, *, reason: str = "") -> int:
    """Revoke every active session for ``actor``. Returns count revoked."""
    now = time.time()
    revoked = 0
    with _LOCK:
        store = _load_sessions_locked()
        tenant_store = store.get(tenant_id, {})
        for sid, sess in list(tenant_store.items()):
            if sess.actor == actor and not sess.revoked:
                updated = replace(sess, revoked=True, revoked_at=now, revoke_reason=reason[:120])
                tenant_store[sid] = updated
                _append_session_locked(updated)
                revoked += 1
    return revoked


def revoke_all_for_tenant(tenant_id: str, *, reason: str = "") -> int:
    """Revoke every active session for the whole workspace."""
    now = time.time()
    revoked = 0
    with _LOCK:
        store = _load_sessions_locked()
        tenant_store = store.get(tenant_id, {})
        for sid, sess in list(tenant_store.items()):
            if not sess.revoked:
                updated = replace(sess, revoked=True, revoked_at=now, revoke_reason=reason[:120])
                tenant_store[sid] = updated
                _append_session_locked(updated)
                revoked += 1
    return revoked


def unrevoke_for_tests_or_self(tenant_id: str, session_id: str) -> Session | None:
    """Restore the caller's own session after a blanket revoke.

    Used by ``POST /sessions/revoke-all`` when ``include_self`` is
    false so the operator who initiated the incident response is not
    locked out of the very console they used to do it.
    """
    with _LOCK:
        store = _load_sessions_locked()
        tenant_store = store.get(tenant_id, {})
        existing = tenant_store.get(session_id)
        if existing is None or not existing.revoked:
            return existing
        restored = replace(existing, revoked=False, revoked_at=0.0, revoke_reason="")
        tenant_store[session_id] = restored
        _append_session_locked(restored)
        return restored


def reset_cache_for_tests() -> None:
    """Drop the in-process caches so a test can repoint settings paths."""
    global _SESSIONS_CACHE, _SESSIONS_CACHE_PATH, _POLICY_CACHE, _POLICY_CACHE_PATH
    with _LOCK:
        _SESSIONS_CACHE = None
        _SESSIONS_CACHE_PATH = None
        _POLICY_CACHE = None
        _POLICY_CACHE_PATH = None


def cap_pat_expiry(tenant_id: str, requested_expires_at: float, now: float | None = None) -> float:
    """Apply the workspace ``max_pat_lifetime_minutes`` cap.

    Returns the (possibly tightened) expires_at. 0.0 means "no expiry"
    on input; if a workspace cap is set, we always return a positive
    expiry so the cap is enforced even on "never expires" requests.
    """
    policy = get_policy(tenant_id)
    if policy.max_pat_lifetime_minutes <= 0:
        return requested_expires_at
    now = time.time() if now is None else now
    ceiling = now + policy.max_pat_lifetime_minutes * 60
    if requested_expires_at <= 0:
        return ceiling
    return min(requested_expires_at, ceiling)
