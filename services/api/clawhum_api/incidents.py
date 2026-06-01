"""Security incident (breach) tracker.

GDPR Art 33 obligates a controller to notify the supervisory authority
"without undue delay and, where feasible, not later than 72 hours" after
becoming aware of a personal data breach. Art 34 obligates direct
notification to affected data subjects when the breach is likely to
result in a high risk to their rights and freedoms. CCPA section
1798.82 and most US state breach laws impose similar duties. SOC2
criterion CC7.3 requires the entity to evaluate security events and
take action.

Procurement reviewers ask "what is your incident response process and
where do you log incidents?" and without a system of record the only
honest answer is "we cannot prove one." This module is that system of
record. A workspace owner can:

* declare a new incident with a severity, a short title, a discovery
  timestamp (defaults to now), and free-text detail
* see the open queue with a 72-hour regulator-notify clock per
  incident so the dashboard surfaces what is approaching the GDPR Art
  33 deadline
* append timeline entries as the response progresses (containment,
  investigation, eradication, recovery) with optional state changes
* advance status through ``open`` then ``contained`` then ``resolved``
  or ``closed_no_action`` (with a required justification on the latter
  so the audit trail explains why no further action was taken)
* mark regulator notification done with the authority name and a
  reference number so the entry is defensible during a regulator
  follow-up

Storage is the JSONL tombstone-and-append pattern shared by the rest
of the admin surface (dsar, security_contacts, sso_store) so
multi-tenant deployments do not require a database to enable incident
intake. Reads collapse the log into the latest state per incident id
and apply tenant scoping at both the route layer and here for defense
in depth.

Status transitions are enforced and an incident that has reached a
terminal state (``resolved`` or ``closed_no_action``) cannot be
re-opened; declare a new incident if a related event surfaces later.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings


_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_LOCK = Lock()
_CACHE: dict[str, dict[str, "Incident"]] | None = None
_CACHE_PATH: Path | None = None

ALLOWED_SEVERITIES = ("low", "medium", "high", "critical")
ALLOWED_STATUSES = ("open", "contained", "resolved", "closed_no_action")
TERMINAL_STATUSES = frozenset({"resolved", "closed_no_action"})
# GDPR Art 33: notify the supervisory authority within 72h.
NOTIFY_DEADLINE_SECONDS = 72 * 3600


@dataclass(frozen=True)
class Event:
    at: float
    actor: str
    kind: str
    note: str
    from_status: str
    to_status: str

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "actor": self.actor,
            "kind": self.kind,
            "note": self.note,
            "from_status": self.from_status,
            "to_status": self.to_status,
        }


@dataclass(frozen=True)
class Incident:
    id: str
    tenant_id: str
    title: str
    severity: str
    status: str
    detail: str
    discovered_at: float
    created_at: float
    updated_at: float
    closed_at: float
    regulator_notified_at: float
    regulator_name: str
    regulator_reference: str
    subjects_notified_at: float
    affected_count: int
    history: tuple[Event, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        now = time.time()
        terminal = self.status in TERMINAL_STATUSES
        notify_deadline = self.discovered_at + NOTIFY_DEADLINE_SECONDS
        notify_due = self.regulator_notified_at == 0.0 and not terminal
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "detail": self.detail,
            "discovered_at": self.discovered_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "regulator_notified_at": self.regulator_notified_at,
            "regulator_name": self.regulator_name,
            "regulator_reference": self.regulator_reference,
            "subjects_notified_at": self.subjects_notified_at,
            "affected_count": self.affected_count,
            "notify_deadline_at": notify_deadline,
            "notify_overdue": notify_due and now > notify_deadline,
            "history": [e.to_dict() for e in self.history],
        }


def _new_id() -> str:
    return "inc_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _path() -> Path:
    return Path(get_settings().incidents_path)


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _validate_severity(sev: str) -> str:
    s = (sev or "").strip().lower()
    if s not in ALLOWED_SEVERITIES:
        raise ValueError(
            "invalid severity; must be one of " + ", ".join(ALLOWED_SEVERITIES)
        )
    return s


def _validate_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        raise ValueError("title is required")
    if len(t) > 200:
        raise ValueError("title must be 200 characters or fewer")
    return t


def _replace(inc: Incident, **kw) -> Incident:
    data = {
        "id": inc.id,
        "tenant_id": inc.tenant_id,
        "title": inc.title,
        "severity": inc.severity,
        "status": inc.status,
        "detail": inc.detail,
        "discovered_at": inc.discovered_at,
        "created_at": inc.created_at,
        "updated_at": inc.updated_at,
        "closed_at": inc.closed_at,
        "regulator_notified_at": inc.regulator_notified_at,
        "regulator_name": inc.regulator_name,
        "regulator_reference": inc.regulator_reference,
        "subjects_notified_at": inc.subjects_notified_at,
        "affected_count": inc.affected_count,
        "history": inc.history,
    }
    data.update(kw)
    return Incident(**data)


def _load_locked() -> dict[str, dict[str, Incident]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, dict[str, Incident]] = {}
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
                iid = str(row.get("id") or "")
                if not iid:
                    continue
                if row.get("_deleted"):
                    out.get(tenant, {}).pop(iid, None)
                    continue
                if row.get("_event"):
                    bucket = out.get(tenant, {})
                    existing = bucket.get(iid)
                    if existing is None:
                        continue
                    ev = Event(
                        at=float(row.get("at") or time.time()),
                        actor=str(row.get("actor") or ""),
                        kind=str(row.get("kind") or ""),
                        note=str(row.get("note") or ""),
                        from_status=str(row.get("from_status") or ""),
                        to_status=str(row.get("to_status") or ""),
                    )
                    patch: dict = {
                        "updated_at": ev.at,
                        "history": existing.history + (ev,),
                    }
                    if ev.to_status:
                        patch["status"] = ev.to_status
                        if (
                            ev.to_status in TERMINAL_STATUSES
                            and existing.closed_at == 0.0
                        ):
                            patch["closed_at"] = ev.at
                    if ev.kind == "regulator_notified":
                        if existing.regulator_notified_at == 0.0:
                            patch["regulator_notified_at"] = ev.at
                        rn = str(row.get("regulator_name") or "")
                        rr = str(row.get("regulator_reference") or "")
                        if rn:
                            patch["regulator_name"] = rn
                        if rr:
                            patch["regulator_reference"] = rr
                    if ev.kind == "subjects_notified":
                        if existing.subjects_notified_at == 0.0:
                            patch["subjects_notified_at"] = ev.at
                        ac = row.get("affected_count")
                        if ac is not None:
                            try:
                                patch["affected_count"] = int(ac)
                            except (TypeError, ValueError):
                                pass
                    bucket[iid] = _replace(existing, **patch)
                    continue
                inc = Incident(
                    id=iid,
                    tenant_id=tenant,
                    title=str(row.get("title") or ""),
                    severity=str(row.get("severity") or "low"),
                    status=str(row.get("status") or "open"),
                    detail=str(row.get("detail") or ""),
                    discovered_at=float(row.get("discovered_at") or 0.0),
                    created_at=float(row.get("created_at") or 0.0),
                    updated_at=float(
                        row.get("updated_at") or row.get("created_at") or 0.0
                    ),
                    closed_at=float(row.get("closed_at") or 0.0),
                    regulator_notified_at=float(
                        row.get("regulator_notified_at") or 0.0
                    ),
                    regulator_name=str(row.get("regulator_name") or ""),
                    regulator_reference=str(
                        row.get("regulator_reference") or ""
                    ),
                    subjects_notified_at=float(
                        row.get("subjects_notified_at") or 0.0
                    ),
                    affected_count=int(row.get("affected_count") or 0),
                    history=(),
                )
                out.setdefault(tenant, {})[iid] = inc
    _CACHE = out
    _CACHE_PATH = p
    return out


def list_incidents(
    tenant_id: str,
    status: str | None = None,
) -> list[Incident]:
    with _LOCK:
        store = _load_locked()
        rows = list(store.get(tenant_id, {}).values())
    if status:
        rows = [r for r in rows if r.status == status]
    sev_order = {s: i for i, s in enumerate(reversed(ALLOWED_SEVERITIES))}
    rows.sort(
        key=lambda r: (
            1 if r.status in TERMINAL_STATUSES else 0,
            -sev_order.get(r.severity, 0),
            r.discovered_at,
        )
    )
    return rows


def get_incident(tenant_id: str, incident_id: str) -> Incident | None:
    with _LOCK:
        store = _load_locked()
        return store.get(tenant_id, {}).get(incident_id)


def _write(records: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def declare_incident(
    tenant_id: str,
    title: str,
    severity: str,
    detail: str = "",
    discovered_at: float | None = None,
    actor: str = "system",
) -> Incident:
    title_norm = _validate_title(title)
    severity_norm = _validate_severity(severity)
    now = time.time()
    discovered = float(discovered_at) if discovered_at else now
    if discovered > now + 60.0:
        raise ValueError("discovered_at cannot be in the future")
    if discovered < now - 365 * 86400.0:
        raise ValueError("discovered_at cannot be more than 365 days in the past")
    inc = Incident(
        id=_new_id(),
        tenant_id=tenant_id,
        title=title_norm,
        severity=severity_norm,
        status="open",
        detail=(detail or "").strip()[:8000],
        discovered_at=discovered,
        created_at=now,
        updated_at=now,
        closed_at=0.0,
        regulator_notified_at=0.0,
        regulator_name="",
        regulator_reference="",
        subjects_notified_at=0.0,
        affected_count=0,
        history=(),
    )
    intake = Event(
        at=now,
        actor=actor or "system",
        kind="declared",
        note=inc.detail,
        from_status="",
        to_status="open",
    )
    with _LOCK:
        _write([
            inc.to_dict(),
            {
                "id": inc.id,
                "tenant_id": tenant_id,
                "_event": True,
                **intake.to_dict(),
            },
        ])
        store = _load_locked()
        store.setdefault(tenant_id, {})[inc.id] = _replace(
            inc, history=(intake,)
        )
        return store[tenant_id][inc.id]


def _require_open(inc: Incident) -> None:
    if inc.status in TERMINAL_STATUSES:
        raise ValueError(
            "incident is already " + inc.status + " and cannot be modified"
        )


def append_note(
    tenant_id: str,
    incident_id: str,
    note: str,
    actor: str,
) -> Incident:
    note_clean = (note or "").strip()[:4000]
    if not note_clean:
        raise ValueError("note is required")
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id, {}).get(incident_id)
        if existing is None:
            raise KeyError("incident not found")
        _require_open(existing)
        now = time.time()
        ev = Event(
            at=now,
            actor=actor or "system",
            kind="note",
            note=note_clean,
            from_status="",
            to_status="",
        )
        _write([{
            "id": incident_id,
            "tenant_id": tenant_id,
            "_event": True,
            **ev.to_dict(),
        }])
        store[tenant_id][incident_id] = _replace(
            existing,
            updated_at=now,
            history=existing.history + (ev,),
        )
        return store[tenant_id][incident_id]


def advance_incident(
    tenant_id: str,
    incident_id: str,
    to_status: str,
    note: str,
    actor: str,
) -> Incident:
    to_norm = (to_status or "").strip().lower()
    if to_norm not in ALLOWED_STATUSES:
        raise ValueError(
            "invalid status; must be one of " + ", ".join(ALLOWED_STATUSES)
        )
    note_clean = (note or "").strip()[:4000]
    if to_norm == "closed_no_action" and not note_clean:
        raise ValueError(
            "closing with no action requires a non-empty justification"
        )
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id, {}).get(incident_id)
        if existing is None:
            raise KeyError("incident not found")
        if existing.status in TERMINAL_STATUSES:
            raise ValueError(
                "incident is already "
                + existing.status
                + " and cannot be reopened"
            )
        if to_norm == existing.status:
            raise ValueError("incident is already in status " + to_norm)
        now = time.time()
        ev = Event(
            at=now,
            actor=actor or "system",
            kind="advanced",
            note=note_clean,
            from_status=existing.status,
            to_status=to_norm,
        )
        _write([{
            "id": incident_id,
            "tenant_id": tenant_id,
            "_event": True,
            **ev.to_dict(),
        }])
        patch: dict = {
            "status": to_norm,
            "updated_at": now,
            "history": existing.history + (ev,),
        }
        if to_norm in TERMINAL_STATUSES and existing.closed_at == 0.0:
            patch["closed_at"] = now
        store[tenant_id][incident_id] = _replace(existing, **patch)
        return store[tenant_id][incident_id]


def record_regulator_notification(
    tenant_id: str,
    incident_id: str,
    regulator_name: str,
    regulator_reference: str,
    note: str,
    actor: str,
) -> Incident:
    name = (regulator_name or "").strip()
    if not name or len(name) > 200:
        raise ValueError("regulator_name is required and must be 1..200 chars")
    reference = (regulator_reference or "").strip()[:200]
    note_clean = (note or "").strip()[:4000]
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id, {}).get(incident_id)
        if existing is None:
            raise KeyError("incident not found")
        if existing.regulator_notified_at != 0.0:
            raise ValueError(
                "regulator notification is already recorded for this incident"
            )
        now = time.time()
        ev = Event(
            at=now,
            actor=actor or "system",
            kind="regulator_notified",
            note=note_clean,
            from_status="",
            to_status="",
        )
        _write([{
            "id": incident_id,
            "tenant_id": tenant_id,
            "_event": True,
            "regulator_name": name,
            "regulator_reference": reference,
            **ev.to_dict(),
        }])
        store[tenant_id][incident_id] = _replace(
            existing,
            updated_at=now,
            regulator_notified_at=now,
            regulator_name=name,
            regulator_reference=reference,
            history=existing.history + (ev,),
        )
        return store[tenant_id][incident_id]


def record_subjects_notification(
    tenant_id: str,
    incident_id: str,
    affected_count: int,
    note: str,
    actor: str,
) -> Incident:
    if affected_count < 0 or affected_count > 10_000_000:
        raise ValueError("affected_count must be between 0 and 10000000")
    note_clean = (note or "").strip()[:4000]
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id, {}).get(incident_id)
        if existing is None:
            raise KeyError("incident not found")
        if existing.subjects_notified_at != 0.0:
            raise ValueError(
                "data subject notification is already recorded for this"
                " incident"
            )
        now = time.time()
        ev = Event(
            at=now,
            actor=actor or "system",
            kind="subjects_notified",
            note=note_clean,
            from_status="",
            to_status="",
        )
        _write([{
            "id": incident_id,
            "tenant_id": tenant_id,
            "_event": True,
            "affected_count": int(affected_count),
            **ev.to_dict(),
        }])
        store[tenant_id][incident_id] = _replace(
            existing,
            updated_at=now,
            subjects_notified_at=now,
            affected_count=int(affected_count),
            history=existing.history + (ev,),
        )
        return store[tenant_id][incident_id]


def summary(tenant_id: str) -> dict:
    rows = list_incidents(tenant_id)
    now = time.time()
    open_rows = [r for r in rows if r.status not in TERMINAL_STATUSES]
    notify_overdue = [
        r for r in open_rows
        if r.regulator_notified_at == 0.0
        and now > r.discovered_at + NOTIFY_DEADLINE_SECONDS
    ]
    by_status = {s: 0 for s in ALLOWED_STATUSES}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    by_severity = {s: 0 for s in ALLOWED_SEVERITIES}
    for r in rows:
        by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
    next_deadline = None
    for r in open_rows:
        if r.regulator_notified_at != 0.0:
            continue
        deadline = r.discovered_at + NOTIFY_DEADLINE_SECONDS
        if next_deadline is None or deadline < next_deadline:
            next_deadline = deadline
    return {
        "open": len(open_rows),
        "total": len(rows),
        "notify_overdue": len(notify_overdue),
        "overdue": len(notify_overdue),
        "by_status": by_status,
        "by_severity": by_severity,
        "next_notify_deadline_at": next_deadline,
    }
