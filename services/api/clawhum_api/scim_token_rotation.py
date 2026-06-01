"""Per-workspace SCIM bearer token maximum age (forced rotation policy).

Why this exists
---------------
Each workspace can mint one long-lived SCIM 2.0 bearer token that the
buyer's identity provider (Okta, Azure AD, Google Workspace) presents
on every joiner / leaver push. The plaintext is shown once at mint
time, then only its SHA-256 hash lives on disk. What the platform did
NOT have until now is a way for a workspace owner to declare a maximum
age for that token and have the API surface that policy in a way IdP
operators can act on without waiting for a real outage.

Enterprise procurement (SOC2 CC6.1, ISO 27001 A.10.1.2, NIST 800-53
SC-12, every modern DPA) requires that long-lived shared secrets get
rotated on a defined cadence. SCIM bearer tokens are the worst kind
of shared secret because they unlock the entire member roster, so a
forced-rotation knob is non-negotiable for buyers running a vendor
security review. The standard fix in HTTP land is the same pair of
headers we already attach for PAT expiry and webhook secret aging:

* ``Sunset`` (RFC 8594) on SCIM responses when the active token has
  crossed the per-workspace age floor, pointing at the original
  deadline that was missed.
* ``Deprecation: true`` (draft-ietf-httpapi-deprecation-header) so
  static gateways can match without parsing.
* ``X-Clawhum-SCIM-Token-Age-Days`` and
  ``X-Clawhum-SCIM-Token-Max-Age-Days`` give IdP-side adapters
  structured numbers without scraping the JSON body.
* Optional ``Link: <docs>; rel="sunset"`` pointing at the rotation
  runbook when the workspace supplies a docs URL.

The policy is strictly per workspace: tenant A flipping the knob has
zero effect on tenant B. Storage follows the append-only JSONL
last-writer-wins pattern used by every other per-workspace policy in
this service so no new infrastructure is required and concurrent
writers stay safe. Token storage itself stays in ``scim_tokens`` and
is never mutated here; this module is policy-only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings


_MIN_DAYS = 0
_MAX_DAYS = 3650  # ten years; effectively unlimited but bounded

_LOCK = Lock()
_CACHE: dict[str, "Policy"] | None = None
_CACHE_PATH: Path | None = None


class InvalidPolicyError(ValueError):
    """Raised when a submitted policy fails validation. User-safe."""


def normalise(raw: int | float | str | None) -> int:
    if raw is None or raw == "":
        return 0
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        raise InvalidPolicyError("max_token_age_days must be an integer")
    if n < _MIN_DAYS or n > _MAX_DAYS:
        raise InvalidPolicyError(
            f"max_token_age_days must be between {_MIN_DAYS} and {_MAX_DAYS}"
        )
    return n


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_token_age_days: int
    docs_url: str
    updated_at: float
    updated_by: str

    @property
    def enforcing(self) -> bool:
        return self.max_token_age_days > 0

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_token_age_days": self.max_token_age_days,
            "docs_url": self.docs_url,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().scim_token_rotation_path)


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
                    days = normalise(row.get("max_token_age_days") or 0)
                except InvalidPolicyError:
                    continue
                out[tid] = Policy(
                    tenant_id=tid,
                    max_token_age_days=days,
                    docs_url=str(row.get("docs_url") or "").strip()[:512],
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


def get_max_token_age_days(tenant_id: str) -> int:
    p = get_policy(tenant_id)
    return p.max_token_age_days if p else 0


def set_policy(
    *,
    tenant_id: str,
    max_token_age_days: int | float | str | None,
    docs_url: str | None,
    updated_by: str,
) -> Policy:
    days = normalise(max_token_age_days)
    url = (docs_url or "").strip()
    if len(url) > 512:
        raise InvalidPolicyError("docs_url must be <= 512 chars")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise InvalidPolicyError("docs_url must start with http:// or https://")
    row = Policy(
        tenant_id=tenant_id,
        max_token_age_days=days,
        docs_url=url,
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


def is_stale(
    *,
    tenant_id: str,
    token_created_at: float,
    now: float | None = None,
) -> bool:
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return False
    if token_created_at <= 0:
        return False
    cur = float(now if now is not None else time.time())
    return (cur - token_created_at) >= pol.max_token_age_days * 86400


def compute_headers(
    *,
    tenant_id: str,
    token_created_at: float,
    now: float | None = None,
) -> dict[str, str]:
    """Return Sunset/Deprecation headers when the active SCIM token is stale.

    Returns an empty dict when no policy is set, when the policy is
    disabled (max=0), when no active token exists (created_at<=0), or
    when the active token is still within the configured floor. That
    way the SCIM router can attach headers unconditionally without
    polluting normal SCIM 200s.
    """
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return {}
    if token_created_at <= 0:
        return {}
    cur = float(now if now is not None else time.time())
    floor_seconds = pol.max_token_age_days * 86400
    age = cur - token_created_at
    if age < floor_seconds:
        return {}
    # The deadline that was missed = creation + floor; that instant is
    # in the past for stale tokens, which matches webhook secret aging
    # so monitoring dashboards can unify both surfaces.
    due_at = token_created_at + floor_seconds
    age_days = int(age // 86400)
    headers: dict[str, str] = {
        "Sunset": formatdate(due_at, usegmt=True),
        "Deprecation": "true",
        "X-Clawhum-SCIM-Token-Age-Days": str(age_days),
        "X-Clawhum-SCIM-Token-Max-Age-Days": str(pol.max_token_age_days),
        "X-Clawhum-SCIM-Token-Created-At": _isoformat_utc(token_created_at),
    }
    if pol.docs_url:
        headers["Link"] = f'<{pol.docs_url}>; rel="sunset"'
    return headers


def _isoformat_utc(ts: float) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


EXPOSED_HEADERS: tuple[str, ...] = (
    "Sunset",
    "Deprecation",
    "X-Clawhum-SCIM-Token-Age-Days",
    "X-Clawhum-SCIM-Token-Max-Age-Days",
    "X-Clawhum-SCIM-Token-Created-At",
)
