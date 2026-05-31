"""Data Subject Access Request (DSAR) tracking.

GDPR Art 15 (right of access), Art 17 (right to erasure), Art 20 (data
portability), and CCPA section 1798.100 all require the controller to
track and respond to data subject requests within a statutory window
(30 days in the EU, 45 days in California). Procurement reviews ask
"how do you intake, log, and prove timely response to DSARs?" and
without a system of record the only honest answer is "we cannot."

This module is that system of record. A workspace owner can:

* file a request on behalf of a data subject (typically forwarded
  from privacy@) with the subject's email, the type of request
  (``access``, ``erasure``, ``portability``, ``rectification``), and
  optional free-text notes
* see the open queue with statutory due dates per request
* advance the request through ``received`` then ``in_progress`` then
  ``completed`` or ``rejected`` (with a required justification on
  reject so the audit trail explains the legal basis)
* read the full history for any request, including who advanced it

Storage is the JSONL tombstone-and-append pattern shared by the rest
of the admin surface (security_contacts, embed_origins, sso_store) so
multi-tenant deployments do not require a database to enable DSAR
intake. Reads collapse the log into the latest state per request id
and apply tenant scoping at both the route layer and here for defense
in depth.

Status transitions are enforced and a request that has reached a
terminal state (``completed`` or ``rejected``) cannot be re-opened;
file a new request instead. This matches how privacy ops teams
actually run the playbook and keeps the audit log honest.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings


_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_LOCK = Lock()
_CACHE: dict[str, dict[str, "Request"]] | None = None
_CACHE_PATH: Path | None = None

ALLOWED_KINDS = ("access", "erasure", "portability", "rectification")
ALLOWED_STATUSES = ("received", "in_progress", "completed", "rejected")
TERMINAL_STATUSES = frozenset({"completed", "rejected"})
# Statutory response windows. We default to the tightest applicable
# (EU GDPR, 30 days) so the dashboard always errs on the safe side.
DEFAULT_DUE_DAYS = 30

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class Event:
    at: float
    actor: str
    action: str
    note: str
    from_status: str
    to_status: str

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "note": self.note,
            "from_status": self.from_status,
            "to_status": self.to_status,
        }


@dataclass(frozen=True)
class Request:
    id: str
    tenant_id: str
    subject_email: str
    kind: str
    status: str
    note: str
    created_at: float
    due_at: float
    updated_at: float
    closed_at: float
    history: tuple[Event, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "subject_email": self.subject_email,
            "kind": self.kind,
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at,
            "due_at": self.due_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "overdue": self.status not in TERMINAL_STATUSES
            and time.time() > self.due_at,
            "history": [e.to_dict() for e in self.history],
        }


def _new_id() -> str:
    return "dsar_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _path() -> Path:
    return Path(get_settings().dsar_requests_path)


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _validate_email(email: str) -> str:
    e = (email or "").strip()
    if not _EMAIL_RE.match(e):
        raise ValueError("invalid subject email address")
    return e.lower()


def _validate_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k not in ALLOWED_KINDS:
        raise ValueError(
            "invalid kind; must be one of " + ", ".join(ALLOWED_KINDS)
        )
    return k


def _replace(req: Request, **kw) -> Request:
    data = {
        "id": req.id,
        "tenant_id": req.tenant_id,
        "subject_email": req.subject_email,
        "kind": req.kind,
        "status": req.status,
        "note": req.note,
        "created_at": req.created_at,
        "due_at": req.due_at,
        "updated_at": req.updated_at,
        "closed_at": req.closed_at,
        "history": req.history,
    }
    data.update(kw)
    return Request(**data)


def _load_locked() -> dict[str, dict[str, Request]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, dict[str, Request]] = {}
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
                tenant = str(row.get("tenant_id") or "default")
                rid = str(row.get("id") or "")
                if not rid:
                    continue
                if row.get("_deleted"):
                    bucket = out.get(tenant, {})
                    bucket.pop(rid, None)
                    continue
                if row.get("_event"):
                    bucket = out.get(tenant, {})
                    existing = bucket.get(rid)
                    if existing is None:
                        continue
                    ev = Event(
                        at=float(row.get("at") or time.time()),
                        actor=str(row.get("actor") or ""),
                        action=str(row.get("action") or ""),
                        note=str(row.get("note") or ""),
                        from_status=str(row.get("from_status") or ""),
                        to_status=str(row.get("to_status") or ""),
                    )
                    new_status = ev.to_status or existing.status
                    closed_at = existing.closed_at
                    if new_status in TERMINAL_STATUSES and closed_at == 0.0:
                        closed_at = ev.at
                    bucket[rid] = _replace(
                        existing,
                        status=new_status,
                        updated_at=ev.at,
                        closed_at=closed_at,
                        history=existing.history + (ev,),
                    )
                    continue
                req = Request(
                    id=rid,
                    tenant_id=tenant,
                    subject_email=str(row.get("subject_email") or ""),
                    kind=str(row.get("kind") or "access"),
                    status=str(row.get("status") or "received"),
                    note=str(row.get("note") or ""),
                    created_at=float(row.get("created_at") or 0.0),
                    due_at=float(row.get("due_at") or 0.0),
                    updated_at=float(
                        row.get("updated_at") or row.get("created_at") or 0.0
                    ),
                    closed_at=float(row.get("closed_at") or 0.0),
                    history=(),
                )
                out.setdefault(tenant, {})[rid] = req
    _CACHE = out
    _CACHE_PATH = p
    return out


def list_requests(
    tenant_id: str,
    status: str | None = None,
) -> list[Request]:
    with _LOCK:
        store = _load_locked()
        rows = list(store.get(tenant_id, {}).values())
    if status:
        rows = [r for r in rows if r.status == status]
    # Open requests first, then by due date ascending so overdue rises
    # to the top of the queue.
    rows.sort(
        key=lambda r: (
            1 if r.status in TERMINAL_STATUSES else 0,
            r.due_at,
        )
    )
    return rows


def get_request(tenant_id: str, request_id: str) -> Request | None:
    with _LOCK:
        store = _load_locked()
        return store.get(tenant_id, {}).get(request_id)


def file_request(
    tenant_id: str,
    subject_email: str,
    kind: str,
    note: str = "",
    actor: str = "system",
    due_days: int = DEFAULT_DUE_DAYS,
) -> Request:
    email_norm = _validate_email(subject_email)
    kind_norm = _validate_kind(kind)
    if due_days <= 0 or due_days > 365:
        raise ValueError("due_days must be between 1 and 365")
    now = time.time()
    req = Request(
        id=_new_id(),
        tenant_id=tenant_id,
        subject_email=email_norm,
        kind=kind_norm,
        status="received",
        note=(note or "").strip()[:2000],
        created_at=now,
        due_at=now + due_days * 86400.0,
        updated_at=now,
        closed_at=0.0,
        history=(),
    )
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    intake = Event(
        at=now,
        actor=actor or "system",
        action="filed",
        note=req.note,
        from_status="",
        to_status="received",
    )
    with _LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(req.to_dict()) + "\n")
            fh.write(json.dumps({
                "id": req.id,
                "tenant_id": tenant_id,
                "_event": True,
                **intake.to_dict(),
            }) + "\n")
        store = _load_locked()
        store.setdefault(tenant_id, {})[req.id] = _replace(
            req, history=(intake,)
        )
        return store[tenant_id][req.id]


def advance_request(
    tenant_id: str,
    request_id: str,
    to_status: str,
    note: str,
    actor: str,
) -> Request:
    to_norm = (to_status or "").strip().lower()
    if to_norm not in ALLOWED_STATUSES:
        raise ValueError(
            "invalid status; must be one of " + ", ".join(ALLOWED_STATUSES)
        )
    note_clean = (note or "").strip()[:2000]
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, {})
        existing = bucket.get(request_id)
        if existing is None:
            raise KeyError("request not found")
        if existing.status in TERMINAL_STATUSES:
            raise ValueError(
                "request is already " + existing.status + " and cannot be reopened"
            )
        if to_norm == existing.status:
            raise ValueError("request is already in status " + to_norm)
        if to_norm == "rejected" and not note_clean:
            raise ValueError("rejection requires a non-empty note")
        now = time.time()
        ev = Event(
            at=now,
            actor=actor or "system",
            action="advanced",
            note=note_clean,
            from_status=existing.status,
            to_status=to_norm,
        )
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": request_id,
                "tenant_id": tenant_id,
                "_event": True,
                **ev.to_dict(),
            }) + "\n")
        closed_at = existing.closed_at
        if to_norm in TERMINAL_STATUSES and closed_at == 0.0:
            closed_at = now
        bucket[request_id] = _replace(
            existing,
            status=to_norm,
            updated_at=now,
            closed_at=closed_at,
            history=existing.history + (ev,),
        )
        return bucket[request_id]


def summary(tenant_id: str) -> dict:
    rows = list_requests(tenant_id)
    open_rows = [r for r in rows if r.status not in TERMINAL_STATUSES]
    now = time.time()
    overdue = [r for r in open_rows if r.due_at < now]
    by_status = {s: 0 for s in ALLOWED_STATUSES}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "open": len(open_rows),
        "overdue": len(overdue),
        "total": len(rows),
        "by_status": by_status,
        "by_kind": {
            k: sum(1 for r in rows if r.kind == k) for k in ALLOWED_KINDS
        },
        "next_due_at": min((r.due_at for r in open_rows), default=None),
    }
