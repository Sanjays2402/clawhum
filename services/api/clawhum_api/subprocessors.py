"""Vendor sub-processor registry and per-workspace acknowledgement.

Every enterprise procurement and EU data protection review asks the
same question: "give us the list of sub-processors who can access
customer personal data, and notify us before you add a new one."
GDPR Art. 28(2) requires the processor (us) to obtain prior written
authorisation from the controller (the customer) before engaging a
new sub-processor; SOC2 CC9.2 and most MSAs translate that into a
documented public list plus an objection window.

This module implements two things that map directly to those clauses:

* A globally published registry of sub-processors (their name, the
  service they provide, where they store data, what categories of
  data they touch, and a link to their DPA). The registry is
  versioned: every mutation bumps a monotonically increasing
  revision number so customers can prove "the list we acknowledged"
  exactly.
* Per workspace acknowledgements (which revision a workspace last
  signed off on) and per workspace notification subscriptions (the
  email addresses we email when the registry changes).

Storage is the JSONL tombstone pattern used by ``security_contacts``,
``invite_domains`` and the rest of the admin modules so a deployment
needs no database to turn this on.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings


_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12

_REG_LOCK = Lock()
_REG_CACHE: tuple[Path, dict[str, "SubProcessor"], int] | None = None

_TENANT_LOCK = Lock()
_TENANT_CACHE: tuple[
    Path,
    dict[str, "Acknowledgement"],
    dict[str, list["Subscription"]],
] | None = None

ALLOWED_STATUS = ("active", "proposed", "removed")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^https?://[^\s<>]+$")


@dataclass(frozen=True)
class SubProcessor:
    id: str
    name: str
    purpose: str
    region: str
    data_categories: tuple[str, ...]
    dpa_url: str
    status: str
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "region": self.region,
            "data_categories": list(self.data_categories),
            "dpa_url": self.dpa_url,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Acknowledgement:
    tenant_id: str
    revision: int
    acknowledged_by: str
    acknowledged_at: float


@dataclass(frozen=True)
class Subscription:
    id: str
    tenant_id: str
    email: str
    created_at: float


def _new_id(prefix: str) -> str:
    return f"{prefix}_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _registry_path() -> Path:
    return Path(get_settings().subprocessors_path)


def _tenant_path() -> Path:
    return Path(get_settings().subprocessor_tenant_path)


def reset_cache() -> None:
    global _REG_CACHE, _TENANT_CACHE
    with _REG_LOCK:
        _REG_CACHE = None
    with _TENANT_LOCK:
        _TENANT_CACHE = None


def _validate_email(email: str) -> str:
    e = email.strip()
    if not _EMAIL_RE.match(e):
        raise ValueError("invalid email address")
    return e.lower()


def _validate_url(url: str) -> str:
    u = url.strip()
    if u and not _URL_RE.match(u):
        raise ValueError("dpa_url must be http(s)")
    return u


def _load_registry_locked() -> tuple[dict[str, SubProcessor], int]:
    global _REG_CACHE
    p = _registry_path()
    if _REG_CACHE is not None and _REG_CACHE[0] == p:
        return _REG_CACHE[1], _REG_CACHE[2]
    rows: dict[str, SubProcessor] = {}
    revision = 0
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
                revision = max(revision, int(row.get("_rev") or 0))
                if row.get("_deleted"):
                    rid = row.get("id")
                    if rid in rows:
                        del rows[rid]
                    continue
                try:
                    sp = SubProcessor(
                        id=str(row["id"]),
                        name=str(row.get("name") or ""),
                        purpose=str(row.get("purpose") or ""),
                        region=str(row.get("region") or ""),
                        data_categories=tuple(row.get("data_categories") or []),
                        dpa_url=str(row.get("dpa_url") or ""),
                        status=str(row.get("status") or "active"),
                        created_at=float(row.get("created_at") or 0.0),
                        updated_at=float(row.get("updated_at") or 0.0),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                rows[sp.id] = sp
    _REG_CACHE = (p, rows, revision)
    return rows, revision


def _load_tenant_locked() -> tuple[
    dict[str, Acknowledgement], dict[str, list[Subscription]]
]:
    global _TENANT_CACHE
    p = _tenant_path()
    if _TENANT_CACHE is not None and _TENANT_CACHE[0] == p:
        return _TENANT_CACHE[1], _TENANT_CACHE[2]
    acks: dict[str, Acknowledgement] = {}
    subs: dict[str, list[Subscription]] = {}
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
                kind = row.get("kind")
                if kind == "ack":
                    tenant = str(row.get("tenant_id") or "")
                    if not tenant:
                        continue
                    acks[tenant] = Acknowledgement(
                        tenant_id=tenant,
                        revision=int(row.get("revision") or 0),
                        acknowledged_by=str(row.get("acknowledged_by") or ""),
                        acknowledged_at=float(row.get("acknowledged_at") or 0.0),
                    )
                elif kind == "sub":
                    tenant = str(row.get("tenant_id") or "")
                    sid = row.get("id")
                    if not tenant or not sid:
                        continue
                    if row.get("_deleted"):
                        bucket = subs.get(tenant, [])
                        subs[tenant] = [s for s in bucket if s.id != sid]
                        continue
                    subs.setdefault(tenant, []).append(
                        Subscription(
                            id=str(sid),
                            tenant_id=tenant,
                            email=str(row.get("email") or ""),
                            created_at=float(row.get("created_at") or 0.0),
                        )
                    )
    _TENANT_CACHE = (p, acks, subs)
    return acks, subs


# Registry mutations -----------------------------------------------------


def list_processors(include_removed: bool = False) -> list[SubProcessor]:
    with _REG_LOCK:
        rows, _ = _load_registry_locked()
        out = list(rows.values())
    if not include_removed:
        out = [r for r in out if r.status != "removed"]
    out.sort(key=lambda r: (r.status != "active", r.name.lower(), r.created_at))
    return out


def current_revision() -> int:
    with _REG_LOCK:
        _, rev = _load_registry_locked()
        return rev


def _write_registry_row(payload: dict) -> int:
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _REG_LOCK:
        _, rev = _load_registry_locked()
        new_rev = rev + 1
        payload["_rev"] = new_rev
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
        # Force a reload on next access.
        global _REG_CACHE
        _REG_CACHE = None
        return new_rev


def add_processor(
    name: str,
    purpose: str,
    region: str,
    data_categories: list[str],
    dpa_url: str,
    status: str = "active",
) -> SubProcessor:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    status_norm = status.strip().lower() or "active"
    if status_norm not in ALLOWED_STATUS:
        raise ValueError(
            f"invalid status; must be one of {', '.join(ALLOWED_STATUS)}"
        )
    dpa = _validate_url(dpa_url)
    cats = tuple(c.strip() for c in data_categories if c and c.strip())
    now = time.time()
    sp = SubProcessor(
        id=_new_id("sp"),
        name=name[:120],
        purpose=purpose.strip()[:240],
        region=region.strip()[:64],
        data_categories=cats,
        dpa_url=dpa,
        status=status_norm,
        created_at=now,
        updated_at=now,
    )
    _write_registry_row(sp.to_dict())
    return sp


def update_processor(
    processor_id: str,
    **fields,
) -> SubProcessor | None:
    with _REG_LOCK:
        rows, _ = _load_registry_locked()
        existing = rows.get(processor_id)
    if existing is None:
        return None
    name = str(fields.get("name", existing.name)).strip() or existing.name
    purpose = str(fields.get("purpose", existing.purpose)).strip()
    region = str(fields.get("region", existing.region)).strip()
    status = str(fields.get("status", existing.status)).strip().lower()
    if status not in ALLOWED_STATUS:
        raise ValueError(
            f"invalid status; must be one of {', '.join(ALLOWED_STATUS)}"
        )
    dpa = _validate_url(str(fields.get("dpa_url", existing.dpa_url)))
    cats_in = fields.get("data_categories", list(existing.data_categories))
    cats = tuple(c.strip() for c in cats_in if c and c.strip())
    updated = SubProcessor(
        id=existing.id,
        name=name[:120],
        purpose=purpose[:240],
        region=region[:64],
        data_categories=cats,
        dpa_url=dpa,
        status=status,
        created_at=existing.created_at,
        updated_at=time.time(),
    )
    _write_registry_row(updated.to_dict())
    return updated


def delete_processor(processor_id: str) -> bool:
    with _REG_LOCK:
        rows, _ = _load_registry_locked()
        if processor_id not in rows:
            return False
    _write_registry_row({"id": processor_id, "_deleted": True})
    return True


# Tenant mutations -------------------------------------------------------


def _write_tenant_row(payload: dict) -> None:
    p = _tenant_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _TENANT_LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
        global _TENANT_CACHE
        _TENANT_CACHE = None


def get_acknowledgement(tenant_id: str) -> Acknowledgement | None:
    with _TENANT_LOCK:
        acks, _ = _load_tenant_locked()
        return acks.get(tenant_id)


def acknowledge(tenant_id: str, actor: str, revision: int) -> Acknowledgement:
    ack = Acknowledgement(
        tenant_id=tenant_id,
        revision=int(revision),
        acknowledged_by=actor[:120],
        acknowledged_at=time.time(),
    )
    _write_tenant_row(
        {
            "kind": "ack",
            "tenant_id": ack.tenant_id,
            "revision": ack.revision,
            "acknowledged_by": ack.acknowledged_by,
            "acknowledged_at": ack.acknowledged_at,
        }
    )
    return ack


def list_subscriptions(tenant_id: str) -> list[Subscription]:
    with _TENANT_LOCK:
        _, subs = _load_tenant_locked()
        rows = list(subs.get(tenant_id, []))
    rows.sort(key=lambda s: (s.email, s.created_at))
    return rows


def add_subscription(tenant_id: str, email: str) -> Subscription:
    email_norm = _validate_email(email)
    with _TENANT_LOCK:
        _, subs = _load_tenant_locked()
        for existing in subs.get(tenant_id, []):
            if existing.email == email_norm:
                raise ValueError("subscription with this email already exists")
    sub = Subscription(
        id=_new_id("ss"),
        tenant_id=tenant_id,
        email=email_norm,
        created_at=time.time(),
    )
    _write_tenant_row(
        {
            "kind": "sub",
            "id": sub.id,
            "tenant_id": sub.tenant_id,
            "email": sub.email,
            "created_at": sub.created_at,
        }
    )
    return sub


def delete_subscription(tenant_id: str, subscription_id: str) -> bool:
    with _TENANT_LOCK:
        _, subs = _load_tenant_locked()
        bucket = subs.get(tenant_id, [])
        if not any(s.id == subscription_id for s in bucket):
            return False
    _write_tenant_row(
        {
            "kind": "sub",
            "id": subscription_id,
            "tenant_id": tenant_id,
            "_deleted": True,
        }
    )
    return True
