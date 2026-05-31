"""Per-workspace PAT scope policy.

Why this exists
---------------
Roles (admin/writer/reader) decide what a human operator can do in
the dashboard. Scopes (read:matches, write:library, write:keys, ...)
are the contract a *machine* credential signs. A common enterprise
procurement question is: "can an admin in my workspace ever mint a
PAT carrying ``write:keys`` or ``admin`` scope?". With nothing in
place, the answer is "yes, because admin implies every scope". With
this module in place, the workspace owner can pin the maximum scope
set their workspace is *ever* allowed to mint, regardless of who is
holding the admin role today.

When at least one scope is registered for a tenant, every PAT mint
must intersect (caller_role_scopes ∩ workspace_policy_scopes). When
no policy is registered the tenant behaves exactly as before, so
existing customers are not broken by this feature.

Storage follows the same append-only JSONL last-writer-wins pattern
the rest of the repo uses (invite_domains, embed_origins,
ip_allowlist), so no new infra is needed and multi-worker writers
remain safe.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

from .api_keys import SCOPES, normalise_scopes

_LOCK = Lock()
_CACHE: dict[str, "Policy"] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    scopes: frozenset[str]  # empty means "no restriction"
    updated_at: float
    updated_by: str  # actor id (api key name or pat id)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "scopes": sorted(self.scopes),
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().scope_policy_path)


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
                    scopes = normalise_scopes(row.get("scopes") or [])
                    out[tid] = Policy(
                        tenant_id=tid,
                        scopes=scopes,
                        updated_at=float(row.get("updated_at") or 0.0),
                        updated_by=str(row.get("updated_by") or ""),
                    )
                except (ValueError, TypeError):
                    continue
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


def has_policy(tenant_id: str) -> bool:
    p = get_policy(tenant_id)
    return p is not None and bool(p.scopes)


def allowed_scopes(tenant_id: str) -> frozenset[str]:
    """Return the policy scope set, or every canonical scope if no policy.

    Callers should intersect their request against this set. An empty
    or missing policy means no restriction, returning ``SCOPES``.
    """
    p = get_policy(tenant_id)
    if p is None or not p.scopes:
        return SCOPES
    return p.scopes


def clamp(tenant_id: str, requested: frozenset[str]) -> frozenset[str]:
    """Intersect requested scopes against the workspace policy."""
    return frozenset(requested) & allowed_scopes(tenant_id)


def set_policy(
    *,
    tenant_id: str,
    scopes,
    updated_by: str,
) -> Policy:
    """Replace the workspace scope policy.

    Pass an empty iterable to clear the policy (back to "no restriction").
    Unknown scopes are silently dropped so a typo cannot widen access.
    """
    cleaned = normalise_scopes(scopes or [])
    row = Policy(
        tenant_id=tenant_id,
        scopes=cleaned,
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


class ScopeNotAllowedError(ValueError):
    """Raised when a PAT mint requests a scope the workspace forbids."""

    def __init__(self, tenant_id: str, denied: frozenset[str]):
        self.tenant_id = tenant_id
        self.denied = frozenset(denied)
        super().__init__(
            "scopes blocked by workspace policy: " + ", ".join(sorted(denied))
        )


def assert_allowed(tenant_id: str, requested: frozenset[str]) -> None:
    """Raise ScopeNotAllowedError when any requested scope is blocked.

    An empty requested set always passes (it means "all scopes the
    PAT's roles allow", which is itself later clamped via ``clamp``).
    """
    if not requested:
        return
    denied = frozenset(requested) - allowed_scopes(tenant_id)
    if denied:
        raise ScopeNotAllowedError(tenant_id, denied)
