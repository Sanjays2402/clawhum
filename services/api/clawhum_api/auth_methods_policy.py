"""Per-workspace allowed authentication methods.

Why this exists
---------------
Three credential types can authenticate against the API today:

* ``env_key``  - static keys loaded from ``CLAWHUM_API_KEYS`` (set by
  ops at deploy time, used by long-lived backend integrations).
* ``pat``      - personal access tokens minted by workspace admins
  from ``/settings/keys``, used by humans and short-lived integrations.
* ``scim``     - SCIM 2.0 bearer tokens used by IdPs (Okta, Azure AD,
  Google Workspace) to push user lifecycle events.

Enterprise security teams routinely require disabling specific
credential classes once a stronger one is in place. The two most
common shapes:

* "After SSO + SCIM rollout, block all PATs so every machine actor
  has to use a service account managed by the IdP."
* "Lock the workspace to PATs only and forbid the deploy-time env
  keys so credential mints are always tied to a named human."

This module stores a per-tenant policy of which methods are
permitted. ``auth.py`` consults it on every authenticated request and
``routes/keys.py`` checks it at mint time so an admin cannot mint a
PAT in a workspace that has disabled PATs.

The policy is also the only place ``scim`` can be turned off per
tenant; the SCIM token store itself is intentionally global so the
IdP integration stays simple.

When no policy is registered the workspace behaves exactly as before
(every method allowed), so existing customers are not broken.

Storage follows the JSONL append-only last-writer-wins pattern used
by ``scope_policy``, ``webhook_policy``, and friends so no new infra
is needed and multi-worker writers stay safe.
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

# Canonical credential class identifiers. Anything outside this set is
# silently dropped on write, matching how scopes/roles are parsed and
# preventing a typo from accidentally narrowing or widening access.
METHODS: frozenset[str] = frozenset({"env_key", "pat", "scim"})
DEFAULT_ALLOWED: frozenset[str] = METHODS


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    methods: frozenset[str]
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "methods": sorted(self.methods),
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().auth_methods_policy_path)


def _normalise(values) -> frozenset[str]:
    if not values:
        return frozenset()
    parts = {str(v).strip().lower() for v in values if v is not None}
    return frozenset(p for p in parts if p in METHODS)


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
                    out[tid] = Policy(
                        tenant_id=tid,
                        methods=_normalise(row.get("methods") or []),
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


def allowed_methods(tenant_id: str) -> frozenset[str]:
    """Return the set of credential classes allowed for the tenant.

    When no policy is registered, every method is allowed. When a
    policy is registered but the methods set is empty (a misconfig
    that would lock everyone out), we treat it as "no restriction"
    too so the workspace can recover by setting the policy again
    from the admin console. The dashboard rejects empty sets at the
    HTTP layer so this branch is defensive only.
    """
    p = get_policy(tenant_id)
    if p is None or not p.methods:
        return DEFAULT_ALLOWED
    return p.methods


def is_allowed(tenant_id: str, method: str) -> bool:
    return method in allowed_methods(tenant_id)


def set_policy(*, tenant_id: str, methods, updated_by: str) -> Policy:
    cleaned = _normalise(methods)
    if not cleaned:
        # Recovery semantics: clearing the policy means "no restriction".
        # We persist it as an explicit empty methods row so the audit
        # trail records the change; allowed_methods() treats empty as
        # DEFAULT_ALLOWED.
        cleaned = frozenset()
    row = Policy(
        tenant_id=tenant_id,
        methods=cleaned,
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


class MethodNotAllowedError(PermissionError):
    """Raised when an action requires a credential class the workspace forbids."""

    def __init__(self, tenant_id: str, method: str):
        self.tenant_id = tenant_id
        self.method = method
        super().__init__(
            f"auth method '{method}' is disabled for this workspace"
        )
