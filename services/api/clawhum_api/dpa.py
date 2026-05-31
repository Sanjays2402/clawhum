"""Per-workspace Data Processing Agreement (DPA) acceptance record.

Why this exists
---------------
Every enterprise procurement review under GDPR Article 28 (and the
mirrored CCPA / UK GDPR / SCC regimes) requires the customer to sign
a Data Processing Agreement before any production data flows. Buyers'
legal and security teams will not start a paid pilot, will not pass
a vendor risk assessment, and will not let an internal team integrate
the API until they can show their auditors a dated, attributed record
that the current DPA version has been accepted by an authorised
person from their workspace.

This module owns that record. Each workspace can:

* See which DPA version is currently published by the vendor.
* See whether their own workspace has accepted it, who clicked
  accept, when, and from which IP and user agent.
* Accept the current version (admin role + fresh MFA only). The
  middleware AuditLogMiddleware already records the mutation in the
  tamper-evident audit chain so the acceptance is forensically
  defensible without extra plumbing here.
* Withdraw acceptance (e.g. they want to re-sign a newer revision
  before letting it auto-bind). Withdrawal is also admin + MFA.

Storage mirrors the invite_domains / ip_allowlist JSONL pattern:
append-only events keyed by workspace, last writer wins, ``_deleted``
rows tombstone earlier acceptances. No database required; multi-worker
safe under the same last-writer-wins semantics every other per-tenant
store in this repo uses.

Tenant isolation
----------------
Every read and every write is gated by ``tenant_id``. A caller for
workspace A can never see, accept, or withdraw on behalf of workspace
B; the integration test ``test_dpa.py`` proves this with a two-tenant
fixture.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "Acceptance"] | None = None
_CACHE_PATH: Path | None = None


# Pinned identifier for the DPA contract version currently published
# by the vendor. Bumping this string (e.g. when legal revises the
# template) invalidates every workspace's prior acceptance so they
# are forced to re-affirm against the new wording.
CURRENT_DPA_VERSION = "2025-05-01"

# Public URL the admin UI links out to so reviewers can read the
# contract before clicking accept. Kept here (not in settings) so a
# misconfigured deploy can't silently point procurement at the wrong
# document.
CURRENT_DPA_URL = (
    "https://github.com/Sanjays2402/clawhum/blob/main/docs/legal/dpa.md"
)


@dataclass(frozen=True)
class Acceptance:
    tenant_id: str
    version: str
    accepted_by: str  # actor id (api key / pat owner)
    accepted_at: float
    ip: str
    user_agent: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "version": self.version,
            "accepted_by": self.accepted_by,
            "accepted_at": self.accepted_at,
            "ip": self.ip,
            "user_agent": self.user_agent,
        }


def _path() -> Path:
    return get_settings().dpa_acceptances_path


def _load(force: bool = False) -> dict[str, Acceptance]:
    global _CACHE, _CACHE_PATH
    target = _path()
    if not force and _CACHE is not None and _CACHE_PATH == target:
        return _CACHE
    state: dict[str, Acceptance] = {}
    if target.exists():
        with open(target, "rb") as f:
            for raw in f:
                try:
                    row = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                tid = row.get("tenant_id")
                if not isinstance(tid, str) or not tid:
                    continue
                if row.get("_deleted"):
                    state.pop(tid, None)
                    continue
                try:
                    state[tid] = Acceptance(
                        tenant_id=tid,
                        version=str(row["version"]),
                        accepted_by=str(row["accepted_by"]),
                        accepted_at=float(row["accepted_at"]),
                        ip=str(row.get("ip", "")),
                        user_agent=str(row.get("user_agent", "")),
                    )
                except (KeyError, ValueError, TypeError):
                    continue
    _CACHE = state
    _CACHE_PATH = target
    return state


def _append(row: dict) -> None:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, separators=(",", ":"), sort_keys=True)
    with open(target, "ab") as f:
        f.write(line.encode("utf-8") + b"\n")


def reset_cache() -> None:
    """Drop the in-memory cache. Tests call this between fixtures."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def get_acceptance(tenant_id: str) -> Acceptance | None:
    """Return the workspace's current acceptance, or None if not yet accepted."""
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("tenant_id required")
    with _LOCK:
        state = _load()
        return state.get(tenant_id)


def accept(
    tenant_id: str,
    *,
    version: str,
    accepted_by: str,
    ip: str = "",
    user_agent: str = "",
) -> Acceptance:
    """Record that ``accepted_by`` accepted ``version`` for ``tenant_id``.

    Rejects acceptance of a version other than ``CURRENT_DPA_VERSION``
    so an outdated client cannot pin the workspace to a stale contract.
    """
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("tenant_id required")
    if not isinstance(version, str) or not version:
        raise ValueError("version required")
    if version != CURRENT_DPA_VERSION:
        raise ValueError(
            f"version mismatch: client sent {version!r}, "
            f"server publishes {CURRENT_DPA_VERSION!r}"
        )
    if not isinstance(accepted_by, str) or not accepted_by:
        raise ValueError("accepted_by required")
    now = time.time()
    rec = Acceptance(
        tenant_id=tenant_id,
        version=version,
        accepted_by=accepted_by,
        accepted_at=now,
        ip=ip or "",
        user_agent=user_agent or "",
    )
    with _LOCK:
        _append(rec.to_dict())
        state = _load(force=True)
    return state[tenant_id]


def withdraw(tenant_id: str) -> bool:
    """Tombstone the current acceptance. Returns True if one existed."""
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("tenant_id required")
    with _LOCK:
        state = _load()
        if tenant_id not in state:
            return False
        _append({"tenant_id": tenant_id, "_deleted": True, "ts": time.time()})
        _load(force=True)
    return True
