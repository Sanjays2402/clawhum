"""Per-workspace minimum security attributes for newly minted PATs.

Why this exists
---------------
SOC2 CC6.1 and ISO 27001 A.9.2.1 require that any logical access
credential carry an identifiable owner, a bounded lifetime, and (when
feasible) network-layer scoping. The existing PAT mint surface
*allows* every one of these (``owner_email``, ``expires_in_days``,
``ip_cidrs``), but does not *require* any of them. Enterprise buyers
ask "can a workspace mint a long lived PAT with no owner attribution
and no IP scope?" and the honest answer today is "yes". This module
lets a workspace admin pin the floor.

Semantics
---------
* All three requirements default to off. Existing customers are not
  broken by enabling this module: until an admin opts in, mint
  behaviour is unchanged.
* ``require_owner_email``: ``POST /keys`` must include a non blank
  ``owner_email`` (already validated for shape by ``pat_store``).
* ``require_expiry``: ``expires_in_days`` must be a positive int and,
  when ``max_expiry_days > 0``, must not exceed it.
* ``require_ip_cidrs``: at least one well formed CIDR must be present
  in ``ip_cidrs``.
* Cross-tenant safety: reads and assertions are keyed by
  ``tenant_id`` and never inspect other tenants' rows.
* Storage follows the same append only JSONL last writer wins pattern
  as ``scope_policy`` and ``pat_concurrency`` so multi-worker writers
  stay safe with no new infra.

Hard ceiling ``MAX_EXPIRY_DAYS`` (10 years) is preserved so a typo in
the admin form cannot effectively disable the cap.
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

# Sanity ceiling: 10 years. Past this the field is effectively "no
# cap" and the operator should set ``max_expiry_days=0`` explicitly.
MAX_EXPIRY_DAYS = 3650


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    require_owner_email: bool
    require_expiry: bool
    max_expiry_days: int  # 0 means "no upper bound"
    require_ip_cidrs: bool
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "require_owner_email": self.require_owner_email,
            "require_expiry": self.require_expiry,
            "max_expiry_days": self.max_expiry_days,
            "require_ip_cidrs": self.require_ip_cidrs,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    @property
    def enforcing(self) -> bool:
        return (
            self.require_owner_email
            or self.require_expiry
            or self.require_ip_cidrs
        )


_EMPTY = Policy(
    tenant_id="",
    require_owner_email=False,
    require_expiry=False,
    max_expiry_days=0,
    require_ip_cidrs=False,
    updated_at=0.0,
    updated_by="",
)


def _path() -> Path:
    return Path(get_settings().pat_min_requirements_path)


def _clamp_days(raw) -> int:
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_EXPIRY_DAYS, n))


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
                out[tid] = Policy(
                    tenant_id=tid,
                    require_owner_email=bool(row.get("require_owner_email")),
                    require_expiry=bool(row.get("require_expiry")),
                    max_expiry_days=_clamp_days(row.get("max_expiry_days")),
                    require_ip_cidrs=bool(row.get("require_ip_cidrs")),
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


def get_policy(tenant_id: str) -> Policy | None:
    with _LOCK:
        return _load_locked().get(tenant_id)


def effective(tenant_id: str) -> Policy:
    """Return the stored policy or an all-off default for this tenant."""
    p = get_policy(tenant_id)
    if p is None:
        return Policy(
            tenant_id=tenant_id,
            require_owner_email=False,
            require_expiry=False,
            max_expiry_days=0,
            require_ip_cidrs=False,
            updated_at=0.0,
            updated_by="",
        )
    return p


def has_policy(tenant_id: str) -> bool:
    p = get_policy(tenant_id)
    return p is not None and p.enforcing


def set_policy(
    *,
    tenant_id: str,
    require_owner_email: bool,
    require_expiry: bool,
    max_expiry_days: int,
    require_ip_cidrs: bool,
    updated_by: str,
) -> Policy:
    """Replace the workspace minimum-requirements policy."""
    row = Policy(
        tenant_id=tenant_id,
        require_owner_email=bool(require_owner_email),
        require_expiry=bool(require_expiry),
        max_expiry_days=_clamp_days(max_expiry_days),
        require_ip_cidrs=bool(require_ip_cidrs),
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


class PatMinRequirementsViolation(ValueError):
    """Raised when a PAT mint does not satisfy the workspace minimums."""

    def __init__(self, tenant_id: str, violations: list[str]):
        self.tenant_id = tenant_id
        self.violations = list(violations)
        super().__init__(
            "pat mint blocked by workspace minimum requirements: "
            + ", ".join(violations)
        )


def assert_compliant(
    *,
    tenant_id: str,
    owner_email: str | None,
    expires_in_days: int | None,
    ip_cidrs,
) -> None:
    """Raise PatMinRequirementsViolation when the mint violates policy.

    No-op when no policy is registered for the tenant or every toggle
    is off, so this stays cheap on the hot path.
    """
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return
    violations: list[str] = []
    if pol.require_owner_email:
        if not (owner_email and owner_email.strip()):
            violations.append("owner_email_required")
    if pol.require_expiry:
        try:
            d = int(expires_in_days) if expires_in_days is not None else 0
        except (TypeError, ValueError):
            d = 0
        if d <= 0:
            violations.append("expiry_required")
        elif pol.max_expiry_days > 0 and d > pol.max_expiry_days:
            violations.append(f"expiry_exceeds_max:{pol.max_expiry_days}")
    if pol.require_ip_cidrs:
        cidrs = list(ip_cidrs or [])
        cleaned = [c for c in cidrs if isinstance(c, str) and c.strip()]
        if not cleaned:
            violations.append("ip_cidrs_required")
    if violations:
        raise PatMinRequirementsViolation(tenant_id, violations)
