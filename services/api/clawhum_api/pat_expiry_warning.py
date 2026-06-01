"""Per-workspace PAT expiry advance-warning policy.

Why this exists
---------------
Personal access tokens are minted with an absolute ``expires_at``
(see :mod:`pat_store`), but SDKs and CI jobs that authenticate with
those tokens have no way to learn that a rotation is due until the
day the token actually stops working. Enterprise procurement asks
for the standard fix:

   "When one of our tokens is within N days of expiry, tell every
   response carrying that token to advertise the upcoming sunset so
   our SDKs can auto-rotate and our pipelines can page on-call
   BEFORE 03:00 outage night."

This module stores a per-workspace ``warn_within_days`` threshold
(default ``0`` = disabled, so existing tenants are unchanged) and
exposes a single helper :func:`compute_headers` that, given the
PAT's absolute expiry, returns the set of standards-compliant
warning headers to attach to the response:

* ``Sunset`` (RFC 8594) -- absolute IMF-fixdate of the expiry.
* ``Deprecation`` (draft-ietf-httpapi-deprecation-header) -- always
  set to ``true`` so static API gateways can match without parsing.
* ``Link: <docs>; rel="sunset"`` -- pointer to the rotation runbook
  surfaced from settings; harmless when the URL is empty (header is
  simply omitted).
* ``X-Clawhum-Token-Expires-In`` -- integer seconds remaining,
  convenient for clients that do not parse Sunset dates.
* ``X-Clawhum-Token-Expires-At`` -- ISO-8601 UTC for the same
  instant, easier for humans reading raw responses.

When ``warn_within_days == 0`` :func:`compute_headers` always
returns an empty dict so the middleware is a no-op for tenants that
have not opted in. When the PAT never expires (``expires_at <= 0``)
the function also returns empty. Cross-tenant lookups are
impossible because the store is keyed by tenant id, mirroring every
other per-workspace policy in this service.
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
_MAX_DAYS = 365

_LOCK = Lock()
_CACHE: dict[str, "Policy"] | None = None
_CACHE_PATH: Path | None = None


class InvalidWarningError(ValueError):
    """Raised when a submitted policy fails validation. User-safe."""


def normalise(raw: int | float | str | None) -> int:
    """Return a sanitised ``warn_within_days`` value.

    Accepts ints/strings; rounds floats down. Out of range raises
    :class:`InvalidWarningError` with a user-safe message.
    """
    if raw is None or raw == "":
        return 0
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        raise InvalidWarningError("warn_within_days must be an integer")
    if n < _MIN_DAYS or n > _MAX_DAYS:
        raise InvalidWarningError(
            f"warn_within_days must be between {_MIN_DAYS} and {_MAX_DAYS}"
        )
    return n


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    warn_within_days: int
    docs_url: str
    updated_at: float
    updated_by: str

    @property
    def enforcing(self) -> bool:
        return self.warn_within_days > 0

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "warn_within_days": self.warn_within_days,
            "docs_url": self.docs_url,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().pat_expiry_warning_path)


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
                    days = normalise(row.get("warn_within_days") or 0)
                except InvalidWarningError:
                    continue
                out[tid] = Policy(
                    tenant_id=tid,
                    warn_within_days=days,
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


def get_warn_within_days(tenant_id: str) -> int:
    p = get_policy(tenant_id)
    return p.warn_within_days if p else 0


def set_policy(
    *,
    tenant_id: str,
    warn_within_days: int | float | str | None,
    docs_url: str | None,
    updated_by: str,
) -> Policy:
    """Replace the workspace PAT expiry warning policy."""
    days = normalise(warn_within_days)
    url = (docs_url or "").strip()
    if len(url) > 512:
        raise InvalidWarningError("docs_url must be <= 512 chars")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise InvalidWarningError("docs_url must start with http:// or https://")
    row = Policy(
        tenant_id=tenant_id,
        warn_within_days=days,
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


def compute_headers(
    *,
    tenant_id: str,
    expires_at: float,
    now: float | None = None,
) -> dict[str, str]:
    """Return the warning headers to attach for a PAT-authenticated response.

    ``expires_at`` is absolute epoch seconds; ``0`` (or negative) means
    "never expires" and yields an empty dict. When no policy is set, or
    when the token is outside the policy's warning window, an empty dict
    is returned so the middleware skips writing any headers.
    """
    if expires_at is None or expires_at <= 0:
        return {}
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return {}
    cur = float(now if now is not None else time.time())
    seconds_until = expires_at - cur
    if seconds_until <= 0:
        # Token is already past expiry; auth will reject the next
        # request anyway. Still surface the headers so a caller
        # holding a freshly expired token sees structured info.
        seconds_until = 0
    if seconds_until > pol.warn_within_days * 86400:
        return {}
    headers: dict[str, str] = {
        # RFC 8594: IMF-fixdate in GMT.
        "Sunset": formatdate(expires_at, usegmt=True),
        # draft-ietf-httpapi-deprecation-header value is "true" or a date.
        "Deprecation": "true",
        "X-Clawhum-Token-Expires-In": str(int(seconds_until)),
        "X-Clawhum-Token-Expires-At": _isoformat_utc(expires_at),
    }
    if pol.docs_url:
        headers["Link"] = f'<{pol.docs_url}>; rel="sunset"'
    return headers


def _isoformat_utc(ts: float) -> str:
    # Avoid importing datetime at module top; cheap to do here.
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# Exposed so the create_app() expose_headers list stays in sync.
EXPOSED_HEADERS: tuple[str, ...] = (
    "Sunset",
    "Deprecation",
    "X-Clawhum-Token-Expires-In",
    "X-Clawhum-Token-Expires-At",
)
