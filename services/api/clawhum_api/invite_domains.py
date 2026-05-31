"""Per-workspace invite email-domain allowlist.

Why this exists
---------------
Enterprise buyers (especially regulated ones) routinely require that
their workspace only ever grant seats to corporate identities. A
common procurement question is "if my admin's account is compromised,
can the attacker invite a personal Gmail and use it as a backdoor?".
The answer they want is "no, because the workspace pins invites to
@acme.com and @acme.co.uk only".

This module owns that pin. It is a per-tenant list of email domains;
when at least one domain is registered, every new invite, SCIM-side
provision, and invite acceptance must resolve to an email whose
domain matches one of the allowed entries. An empty list means
"no restriction" so existing tenants keep working unchanged.

Storage mirrors the embed_origins / ip_allowlist JSONL pattern:
append-only events keyed by id, last writer wins, ``_deleted`` rows
tombstone earlier ones. No database required; multi-worker safe under
the same last-writer-wins semantics every other per-tenant store in
this repo uses.

The matcher is exact, lowercased, and supports an optional
``include_subdomains`` flag so an admin can pin ``acme.com`` and
allow ``alice@us.acme.com`` without listing every regional subdomain
individually. Subdomain matching only relaxes the rule downward; it
never lets ``evil-acme.com`` slip past ``acme.com``.
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

_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_CACHE: dict[str, list["Domain"]] | None = None
_CACHE_PATH: Path | None = None

# RFC 1035-ish: labels are alnum + hyphen, separated by dots. We accept
# the common subset real corporate domains use and reject everything
# else so a typo cannot silently widen the allowlist.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

MAX_DOMAINS_PER_TENANT = 64


@dataclass(frozen=True)
class Domain:
    id: str
    tenant_id: str
    domain: str  # canonical lowercase, no leading @ or dot
    include_subdomains: bool
    label: str
    created_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "domain": self.domain,
            "include_subdomains": self.include_subdomains,
            "label": self.label,
            "created_at": self.created_at,
        }


def _new_id() -> str:
    return "idom_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def normalize_domain(raw: str) -> str:
    """Return canonical lowercase domain or raise ValueError.

    Accepts ``acme.com``, ``ACME.COM``, ``@acme.com`` and trims
    surrounding whitespace. Rejects anything that does not look like a
    real DNS name so an admin cannot accidentally pin ``"gmail"`` and
    let every Gmail address through.
    """
    if raw is None:
        raise ValueError("domain is required")
    s = str(raw).strip().lower()
    if s.startswith("@"):
        s = s[1:]
    s = s.strip(".")
    if not s:
        raise ValueError("domain is required")
    if not _DOMAIN_RE.match(s):
        raise ValueError("domain must look like acme.com")
    return s


def _email_domain(email: str) -> str:
    if "@" not in email:
        raise ValueError("invalid email")
    return email.rsplit("@", 1)[1].strip().lower()


def _path() -> Path:
    return Path(get_settings().invite_domains_path)


def _load_locked() -> dict[str, list[Domain]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, list[Domain]] = {}
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
                    rid = row.get("id")
                    if rid:
                        for bucket in out.values():
                            bucket[:] = [d for d in bucket if d.id != rid]
                    continue
                try:
                    d = Domain(
                        id=str(row["id"]),
                        tenant_id=str(row.get("tenant_id") or "default"),
                        domain=str(row["domain"]),
                        include_subdomains=bool(row.get("include_subdomains", False)),
                        label=str(row.get("label") or ""),
                        created_at=float(row.get("created_at") or 0.0),
                    )
                except (KeyError, ValueError, TypeError):
                    continue
                out.setdefault(d.tenant_id, []).append(d)
    _CACHE = out
    _CACHE_PATH = p
    return out


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def list_domains(tenant_id: str) -> list[Domain]:
    with _LOCK:
        store = _load_locked()
        return list(store.get(tenant_id, []))


def has_rules(tenant_id: str) -> bool:
    return bool(list_domains(tenant_id))


def add_domain(
    tenant_id: str,
    raw_domain: str,
    *,
    include_subdomains: bool = False,
    label: str = "",
) -> Domain:
    canonical = normalize_domain(raw_domain)
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        for existing in bucket:
            if existing.domain == canonical and existing.include_subdomains == bool(
                include_subdomains
            ):
                return existing
        if len(bucket) >= MAX_DOMAINS_PER_TENANT:
            raise ValueError(
                f"too many domains (max {MAX_DOMAINS_PER_TENANT} per workspace)"
            )
        row = Domain(
            id=_new_id(),
            tenant_id=tenant_id,
            domain=canonical,
            include_subdomains=bool(include_subdomains),
            label=label.strip()[:120],
            created_at=time.time(),
        )
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row.to_dict()) + "\n")
        store.setdefault(tenant_id, []).append(row)
        return row


def delete_domain(tenant_id: str, domain_id: str) -> bool:
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        target = next((d for d in bucket if d.id == domain_id), None)
        if target is None:
            return False
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"id": domain_id, "tenant_id": tenant_id, "_deleted": True}
                )
                + "\n"
            )
        store[tenant_id] = [d for d in bucket if d.id != domain_id]
    return True


def is_email_allowed(tenant_id: str, email: str) -> bool:
    """True when the email matches the tenant policy.

    Empty rule set means "no restriction" so existing tenants keep
    working. Otherwise the email's domain must match one rule
    exactly, or fall under a rule with ``include_subdomains=True``.
    """
    rules = list_domains(tenant_id)
    if not rules:
        return True
    try:
        edom = _email_domain(email)
    except ValueError:
        return False
    for rule in rules:
        if edom == rule.domain:
            return True
        if rule.include_subdomains and edom.endswith("." + rule.domain):
            return True
    return False


class InviteDomainNotAllowedError(ValueError):
    """Raised when an email does not match the workspace invite policy."""

    def __init__(self, email: str, tenant_id: str):
        self.email = email
        self.tenant_id = tenant_id
        super().__init__(
            f"email {email!r} is not in this workspace's invite domain allowlist"
        )


def assert_allowed(tenant_id: str, email: str) -> None:
    """Raise InviteDomainNotAllowedError if the email is blocked."""
    if not is_email_allowed(tenant_id, email):
        raise InviteDomainNotAllowedError(email, tenant_id)
