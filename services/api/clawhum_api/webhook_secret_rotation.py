"""Per-workspace webhook signing-secret maximum age (forced rotation policy).

Why this exists
---------------
Each webhook destination has an HMAC signing secret minted at create
time and rotatable on demand via ``POST /webhooks/{id}/rotate-secret``
with an optional dual-sign grace window. What the platform did NOT
have until now is a way for a workspace owner to declare a maximum
age for those secrets and have the API surface that policy in a way
clients can act on.

Enterprise procurement (SOC2 CC6.1, ISO 27001 A.10.1.2, NIST 800-53
SC-12, every modern DPA) requires that long-lived shared secrets get
rotated on a defined cadence. The standard fix in HTTP land is the
same pair of headers we already attach for PAT expiry warnings:

* ``Sunset`` (RFC 8594) on responses that enumerate webhooks whose
  secret has crossed the policy floor, pointing at the oldest
  affected secret so dashboards can prioritise it.
* ``Deprecation: true`` (draft-ietf-httpapi-deprecation-header) so
  static gateways can match without parsing.
* ``X-Clawhum-Webhook-Secret-Stale-Count`` and
  ``X-Clawhum-Webhook-Secret-Max-Age-Days`` give SDKs structured
  numbers without needing to scrape the JSON body.
* Optional ``Link: <docs>; rel="sunset"`` pointing at the rotation
  runbook when the workspace supplies a docs URL.

The policy is strictly per workspace: tenant A flipping the knob has
zero effect on tenant B. Storage follows the append-only JSONL
last-writer-wins pattern used by every other per-workspace policy
in this service so no new infrastructure is required and concurrent
writers stay safe.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from threading import Lock
from typing import Iterable

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
        raise InvalidPolicyError("max_secret_age_days must be an integer")
    if n < _MIN_DAYS or n > _MAX_DAYS:
        raise InvalidPolicyError(
            f"max_secret_age_days must be between {_MIN_DAYS} and {_MAX_DAYS}"
        )
    return n


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    max_secret_age_days: int
    docs_url: str
    updated_at: float
    updated_by: str

    @property
    def enforcing(self) -> bool:
        return self.max_secret_age_days > 0

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "max_secret_age_days": self.max_secret_age_days,
            "docs_url": self.docs_url,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().webhook_secret_rotation_path)


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
                    days = normalise(row.get("max_secret_age_days") or 0)
                except InvalidPolicyError:
                    continue
                out[tid] = Policy(
                    tenant_id=tid,
                    max_secret_age_days=days,
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


def get_max_secret_age_days(tenant_id: str) -> int:
    p = get_policy(tenant_id)
    return p.max_secret_age_days if p else 0


def set_policy(
    *,
    tenant_id: str,
    max_secret_age_days: int | float | str | None,
    docs_url: str | None,
    updated_by: str,
) -> Policy:
    days = normalise(max_secret_age_days)
    url = (docs_url or "").strip()
    if len(url) > 512:
        raise InvalidPolicyError("docs_url must be <= 512 chars")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise InvalidPolicyError("docs_url must start with http:// or https://")
    row = Policy(
        tenant_id=tenant_id,
        max_secret_age_days=days,
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


def secret_age_anchor(hook: dict) -> float:
    """Epoch timestamp of the most recent successful rotation.

    Falls back to ``created_at`` for hooks that have never rotated.
    A previously-rotated hook is anchored on ``rotated_at`` so the
    new secret gets a fresh max-age clock.
    """
    return float(hook.get("rotated_at") or hook.get("created_at") or 0.0)


def is_stale(
    *,
    tenant_id: str,
    hook: dict,
    now: float | None = None,
) -> bool:
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return False
    anchor = secret_age_anchor(hook)
    if anchor <= 0:
        return False
    cur = float(now if now is not None else time.time())
    return (cur - anchor) >= pol.max_secret_age_days * 86400


def stale_hooks(
    *,
    tenant_id: str,
    hooks: Iterable[dict],
    now: float | None = None,
) -> list[dict]:
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return []
    cur = float(now if now is not None else time.time())
    out: list[dict] = []
    for h in hooks:
        anchor = secret_age_anchor(h)
        if anchor <= 0:
            continue
        if (cur - anchor) >= pol.max_secret_age_days * 86400:
            out.append(h)
    return out


def compute_headers(
    *,
    tenant_id: str,
    hooks: Iterable[dict],
    now: float | None = None,
) -> dict[str, str]:
    """Return the warning headers to attach when at least one hook is stale.

    When no policy is set, no policy is enforcing, or no hooks are
    stale, returns an empty dict so the caller can attach unconditionally
    without polluting normal responses.
    """
    pol = get_policy(tenant_id)
    if pol is None or not pol.enforcing:
        return {}
    cur = float(now if now is not None else time.time())
    floor_seconds = pol.max_secret_age_days * 86400
    stale: list[tuple[float, dict]] = []
    for h in hooks:
        anchor = secret_age_anchor(h)
        if anchor <= 0:
            continue
        age = cur - anchor
        if age >= floor_seconds:
            stale.append((anchor, h))
    if not stale:
        return {}
    # Oldest secret first so the Sunset date highlights the worst
    # offender; that is the one that should be rotated next.
    stale.sort(key=lambda t: t[0])
    oldest_anchor = stale[0][0]
    # The "due" instant for the oldest hook is anchor + floor; that
    # has already passed for stale hooks (by definition), so the
    # Sunset header surfaces the original deadline that was missed.
    due_at = oldest_anchor + floor_seconds
    headers: dict[str, str] = {
        "Sunset": formatdate(due_at, usegmt=True),
        "Deprecation": "true",
        "X-Clawhum-Webhook-Secret-Stale-Count": str(len(stale)),
        "X-Clawhum-Webhook-Secret-Max-Age-Days": str(pol.max_secret_age_days),
        "X-Clawhum-Webhook-Secret-Oldest-Rotated-At": _isoformat_utc(oldest_anchor),
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
    "X-Clawhum-Webhook-Secret-Stale-Count",
    "X-Clawhum-Webhook-Secret-Max-Age-Days",
    "X-Clawhum-Webhook-Secret-Oldest-Rotated-At",
)
