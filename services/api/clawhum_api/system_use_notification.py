"""Per-workspace system-use notification (login banner) with required ack.

Why this exists
---------------
NIST 800-53 AC-8 and the equivalent SOC2 / FedRAMP / DoD controls
require regulated buyers to display a "system use notification" before
a user is allowed to act on the system. The banner names the system,
the data it processes, the monitoring in place, and the consent the
user grants by continuing. Enterprise procurement reviews routinely
ask vendors to prove that an unacknowledged user cannot mutate data.

This module implements the boring half of that control:

* A per-tenant banner record with a monotonically increasing
  ``revision`` that bumps every time the text changes. Old acks are
  invalidated automatically so a material wording change forces every
  actor to re-acknowledge.
* A per-actor ack log keyed by ``(tenant_id, actor_id, revision)``.
  ``actor_id`` is the API key name or PAT id surfaced by ``auth.py``,
  matching how the audit log identifies the principal.

The HTTP edge enforces the ack on every mutating request via
``SystemUseNotificationMiddleware`` so half-built features cannot
forget to call this check; the admin console at
``/admin/system-use-notification`` configures the banner.

Storage follows the JSONL append-only last-writer-wins pattern used
by every other per-workspace policy in this service so no new infra
is needed and multi-worker writers stay safe.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

# Hard caps so a typo in the admin console cannot blow up the
# database or wedge a request log. The wording cap matches the
# real-world DoD warning banner length plus headroom.
MAX_TITLE_LEN = 200
MAX_BODY_LEN = 8000

_BANNER_LOCK = Lock()
_BANNER_CACHE: dict[str, "Banner"] | None = None
_BANNER_CACHE_PATH: Path | None = None

_ACK_LOCK = Lock()
_ACK_CACHE: dict[tuple[str, str], int] | None = None
_ACK_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class Banner:
    tenant_id: str
    revision: int
    title: str
    body: str
    enforced: bool
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "revision": self.revision,
            "title": self.title,
            "body": self.body,
            "enforced": self.enforced,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class Acknowledgement:
    tenant_id: str
    actor_id: str
    revision: int
    acked_at: float
    ip: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "revision": self.revision,
            "acked_at": self.acked_at,
            "ip": self.ip,
        }


def _banner_path() -> Path:
    return Path(get_settings().system_use_notification_path)


def _ack_path() -> Path:
    return Path(get_settings().system_use_acks_path)


def _load_banners_locked() -> dict[str, Banner]:
    global _BANNER_CACHE, _BANNER_CACHE_PATH
    p = _banner_path()
    if _BANNER_CACHE is not None and _BANNER_CACHE_PATH == p:
        return _BANNER_CACHE
    out: dict[str, Banner] = {}
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
                    out[tid] = Banner(
                        tenant_id=tid,
                        revision=int(row.get("revision") or 0),
                        title=str(row.get("title") or "")[:MAX_TITLE_LEN],
                        body=str(row.get("body") or "")[:MAX_BODY_LEN],
                        enforced=bool(row.get("enforced", True)),
                        updated_at=float(row.get("updated_at") or 0.0),
                        updated_by=str(row.get("updated_by") or ""),
                    )
                except (ValueError, TypeError):
                    continue
    _BANNER_CACHE = out
    _BANNER_CACHE_PATH = p
    return out


def _load_acks_locked() -> dict[tuple[str, str], int]:
    """Map (tenant_id, actor_id) -> highest acknowledged revision."""
    global _ACK_CACHE, _ACK_CACHE_PATH
    p = _ack_path()
    if _ACK_CACHE is not None and _ACK_CACHE_PATH == p:
        return _ACK_CACHE
    out: dict[tuple[str, str], int] = {}
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
                aid = str(row.get("actor_id") or "")
                if not tid or not aid:
                    continue
                try:
                    rev = int(row.get("revision") or 0)
                except (ValueError, TypeError):
                    continue
                prev = out.get((tid, aid), 0)
                if rev > prev:
                    out[(tid, aid)] = rev
    _ACK_CACHE = out
    _ACK_CACHE_PATH = p
    return out


def reset_cache() -> None:
    global _BANNER_CACHE, _BANNER_CACHE_PATH, _ACK_CACHE, _ACK_CACHE_PATH
    with _BANNER_LOCK:
        _BANNER_CACHE = None
        _BANNER_CACHE_PATH = None
    with _ACK_LOCK:
        _ACK_CACHE = None
        _ACK_CACHE_PATH = None


def get_banner(tenant_id: str) -> Banner | None:
    with _BANNER_LOCK:
        return _load_banners_locked().get(tenant_id)


def set_banner(
    *,
    tenant_id: str,
    title: str,
    body: str,
    enforced: bool,
    updated_by: str,
) -> Banner:
    """Upsert the banner. Revision bumps only when title or body change.

    Toggling ``enforced`` without changing wording keeps the existing
    revision so admins can pause enforcement without forcing every
    actor to re-ack. Trimming whitespace before comparison prevents
    accidental rev bumps from a stray newline.
    """
    title = (title or "").strip()[:MAX_TITLE_LEN]
    body = (body or "").strip()[:MAX_BODY_LEN]
    with _BANNER_LOCK:
        current = _load_banners_locked().get(tenant_id)
        if current and current.title == title and current.body == body:
            revision = current.revision
        else:
            revision = (current.revision if current else 0) + 1
        row = Banner(
            tenant_id=tenant_id,
            revision=revision,
            title=title,
            body=body,
            enforced=bool(enforced),
            updated_at=time.time(),
            updated_by=(updated_by or "").strip()[:64] or "unknown",
        )
        p = _banner_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row.to_dict()) + "\n")
        store = _load_banners_locked()
        store[tenant_id] = row
    return row


def acked_revision(tenant_id: str, actor_id: str) -> int:
    if not tenant_id or not actor_id:
        return 0
    with _ACK_LOCK:
        return _load_acks_locked().get((tenant_id, actor_id), 0)


def record_ack(
    *,
    tenant_id: str,
    actor_id: str,
    revision: int,
    ip: str,
) -> Acknowledgement:
    actor_id = (actor_id or "").strip()[:128]
    if not actor_id:
        actor_id = "unknown"
    row = Acknowledgement(
        tenant_id=tenant_id,
        actor_id=actor_id,
        revision=int(revision),
        acked_at=time.time(),
        ip=(ip or "").strip()[:64],
    )
    with _ACK_LOCK:
        p = _ack_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row.to_dict()) + "\n")
        store = _load_acks_locked()
        prev = store.get((tenant_id, actor_id), 0)
        if row.revision > prev:
            store[(tenant_id, actor_id)] = row.revision
    return row


def list_acks(tenant_id: str) -> list[dict]:
    """Return the latest ack per actor for a tenant, newest first."""
    p = _ack_path()
    rows: dict[str, dict] = {}
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
                if str(row.get("tenant_id") or "") != tenant_id:
                    continue
                aid = str(row.get("actor_id") or "")
                if not aid:
                    continue
                prev = rows.get(aid)
                if prev is None or float(row.get("acked_at") or 0.0) >= float(
                    prev.get("acked_at") or 0.0
                ):
                    rows[aid] = row
    out = list(rows.values())
    out.sort(key=lambda r: float(r.get("acked_at") or 0.0), reverse=True)
    return out


def needs_ack(tenant_id: str, actor_id: str) -> Banner | None:
    """Return the banner the actor still needs to acknowledge, else None.

    Returns ``None`` (no enforcement) when:

    * No banner has been configured for the tenant.
    * The banner exists but ``enforced`` is False.
    * The banner's text is empty (admins can stage wording before
      flipping enforcement).
    * The actor has already acknowledged the current revision.
    """
    if not tenant_id or not actor_id:
        return None
    banner = get_banner(tenant_id)
    if banner is None or not banner.enforced:
        return None
    if not (banner.title or banner.body):
        return None
    if acked_revision(tenant_id, actor_id) >= banner.revision:
        return None
    return banner
