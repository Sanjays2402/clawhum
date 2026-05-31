"""Per-workspace security and breach notification contacts.

Enterprise buyers and EU customers require a documented contact path
for security incidents (GDPR Art 33 mandates that processors notify
controllers "without undue delay" on becoming aware of a personal data
breach; SOC2 CC7.4 requires defined incident communication channels).
This module stores the people the customer wants us to notify, scoped
per workspace so a tenant cannot see or modify another tenant's roster.

Each contact has:

* ``email`` (required, validated as a basic addr-spec)
* ``name`` (display only)
* ``role`` -- one of ``security``, ``privacy``, ``legal``, ``ops``
* ``phone`` (optional, free-form so international formats work)
* ``primary`` -- exactly one contact per workspace may be marked
  primary; promoting a new one demotes the previous primary.

Storage is the JSONL tombstone pattern used by the rest of the admin
modules (IP allowlist, embed origins, invite domains) so multi-tenant
deployments do not need a database to turn this on.
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
_LOCK = Lock()
_CACHE: dict[str, list["Contact"]] | None = None
_CACHE_PATH: Path | None = None

ALLOWED_ROLES = ("security", "privacy", "legal", "ops")

# Pragmatic email shape: ``local@host.tld``. We deliberately do not try
# to be RFC 5322 compliant; the goal is to reject obvious garbage at
# the admin console, not to validate every legal address.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class Contact:
    id: str
    tenant_id: str
    email: str
    name: str
    role: str
    phone: str
    primary: bool
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "phone": self.phone,
            "primary": self.primary,
            "created_at": self.created_at,
        }


def _new_id() -> str:
    return "sc_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _path() -> Path:
    return Path(get_settings().security_contacts_path)


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _load_locked() -> dict[str, list[Contact]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, list[Contact]] = {}
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
                if row.get("_deleted"):
                    cid = row.get("id")
                    if cid:
                        for bucket in out.values():
                            bucket[:] = [c for c in bucket if c.id != cid]
                    continue
                if row.get("_promote"):
                    # Re-apply primary promotion: clear other primaries
                    # in the same tenant, then mark the target primary.
                    tenant = str(row.get("tenant_id") or "default")
                    target_id = str(row.get("id"))
                    bucket = out.get(tenant, [])
                    new_bucket: list[Contact] = []
                    for c in bucket:
                        if c.id == target_id:
                            new_bucket.append(_replace_primary(c, True))
                        elif c.primary:
                            new_bucket.append(_replace_primary(c, False))
                        else:
                            new_bucket.append(c)
                    out[tenant] = new_bucket
                    continue
                try:
                    email = str(row["email"])
                except KeyError:
                    continue
                contact = Contact(
                    id=str(row["id"]),
                    tenant_id=str(row.get("tenant_id") or "default"),
                    email=email,
                    name=str(row.get("name") or ""),
                    role=str(row.get("role") or "security"),
                    phone=str(row.get("phone") or ""),
                    primary=bool(row.get("primary") or False),
                    created_at=float(row.get("created_at") or 0.0),
                )
                out.setdefault(contact.tenant_id, []).append(contact)
    _CACHE = out
    _CACHE_PATH = p
    return out


def _replace_primary(c: Contact, primary: bool) -> Contact:
    return Contact(
        id=c.id,
        tenant_id=c.tenant_id,
        email=c.email,
        name=c.name,
        role=c.role,
        phone=c.phone,
        primary=primary,
        created_at=c.created_at,
    )


def _validate_email(email: str) -> str:
    e = email.strip()
    if not _EMAIL_RE.match(e):
        raise ValueError("invalid email address")
    return e.lower()


def list_contacts(tenant_id: str) -> list[Contact]:
    with _LOCK:
        store = _load_locked()
        # Sort: primary first, then by creation time so the admin UI is
        # stable and the on-call contact is obvious at a glance.
        rows = list(store.get(tenant_id, []))
        rows.sort(key=lambda c: (0 if c.primary else 1, c.created_at))
        return rows


def add_contact(
    tenant_id: str,
    email: str,
    name: str = "",
    role: str = "security",
    phone: str = "",
    primary: bool = False,
) -> Contact:
    email_norm = _validate_email(email)
    role_norm = role.strip().lower() or "security"
    if role_norm not in ALLOWED_ROLES:
        raise ValueError(
            f"invalid role; must be one of {', '.join(ALLOWED_ROLES)}"
        )
    contact = Contact(
        id=_new_id(),
        tenant_id=tenant_id,
        email=email_norm,
        name=name.strip()[:120],
        role=role_norm,
        phone=phone.strip()[:64],
        primary=False,  # primary is set via promote, never on create
        created_at=time.time(),
    )
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        # Reject duplicates within the same tenant. Cross-tenant
        # duplicates are fine (and expected: two customers may share
        # an SRE on-call email).
        store = _load_locked()
        for existing in store.get(tenant_id, []):
            if existing.email == email_norm:
                raise ValueError("contact with this email already exists")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(contact.to_dict()) + "\n")
        store.setdefault(tenant_id, []).append(contact)
    if primary:
        promote_primary(tenant_id, contact.id)
        # Re-read so the caller sees primary=True on the returned row.
        with _LOCK:
            store = _load_locked()
            for c in store.get(tenant_id, []):
                if c.id == contact.id:
                    return c
    return contact


def delete_contact(tenant_id: str, contact_id: str) -> bool:
    p = _path()
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        target = next((c for c in bucket if c.id == contact_id), None)
        if target is None:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": contact_id,
                "tenant_id": tenant_id,
                "_deleted": True,
            }) + "\n")
        store[tenant_id] = [c for c in bucket if c.id != contact_id]
    return True


def promote_primary(tenant_id: str, contact_id: str) -> bool:
    """Mark ``contact_id`` as primary, demoting any other primary in
    the same tenant. Returns True when the contact existed."""
    p = _path()
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        target = next((c for c in bucket if c.id == contact_id), None)
        if target is None:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": contact_id,
                "tenant_id": tenant_id,
                "_promote": True,
            }) + "\n")
        new_bucket: list[Contact] = []
        for c in bucket:
            if c.id == contact_id:
                new_bucket.append(_replace_primary(c, True))
            elif c.primary:
                new_bucket.append(_replace_primary(c, False))
            else:
                new_bucket.append(c)
        store[tenant_id] = new_bucket
    return True


def get_primary(tenant_id: str) -> Contact | None:
    for c in list_contacts(tenant_id):
        if c.primary:
            return c
    return None
