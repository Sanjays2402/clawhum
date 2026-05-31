"""Workspace IP allowlist.

Each tenant can register one or more CIDR rules. When a tenant has any
rules configured, every authenticated request from that tenant must
originate from a client IP that matches at least one rule, or it is
rejected with 403. An empty rule set means "no restriction" so the
feature is strictly opt-in and existing tenants keep working unchanged.

Storage is the same JSONL pattern used by webhooks/PATs so multi-tenant
deployments do not need a database to enable this control. Reads cache
the parsed rules in process and invalidate on every write. Lookups are
O(rules-per-tenant) which is fine for the small lists this feature is
designed for (a handful of office or VPN ranges).

Why a separate module: keeping the store dependency-free of FastAPI
lets ``auth.require_api_key`` import it without circular imports and
lets unit tests exercise the matcher without spinning up the app.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Iterable

from clawhum_core.settings import get_settings

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_LOCK = Lock()
_CACHE: dict[str, list["Rule"]] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    tenant_id: str
    cidr: str
    label: str
    created_at: float
    network: ipaddress.IPv4Network | ipaddress.IPv6Network = field(compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "cidr": self.cidr,
            "label": self.label,
            "created_at": self.created_at,
        }


def _new_id() -> str:
    return "ip_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _parse_cidr(raw: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    # strict=False so 192.168.1.5/24 is accepted as the surrounding network.
    return ipaddress.ip_network(raw.strip(), strict=False)


def _path() -> Path:
    return Path(get_settings().ip_allowlist_path)


def _load_locked() -> dict[str, list[Rule]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, list[Rule]] = {}
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
                            bucket[:] = [r for r in bucket if r.id != rid]
                    continue
                try:
                    network = _parse_cidr(str(row["cidr"]))
                except (KeyError, ValueError):
                    continue
                rule = Rule(
                    id=str(row["id"]),
                    tenant_id=str(row.get("tenant_id") or "default"),
                    cidr=str(row["cidr"]),
                    label=str(row.get("label") or ""),
                    created_at=float(row.get("created_at") or 0.0),
                    network=network,
                )
                out.setdefault(rule.tenant_id, []).append(rule)
    _CACHE = out
    _CACHE_PATH = p
    return out


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def list_rules(tenant_id: str) -> list[Rule]:
    with _LOCK:
        store = _load_locked()
        return list(store.get(tenant_id, []))


def add_rule(tenant_id: str, cidr: str, label: str = "") -> Rule:
    network = _parse_cidr(cidr)  # raises ValueError on bad input
    rule = Rule(
        id=_new_id(),
        tenant_id=tenant_id,
        cidr=str(network),
        label=label.strip()[:120],
        created_at=time.time(),
        network=network,
    )
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rule.to_dict()) + "\n")
        store = _load_locked()
        store.setdefault(tenant_id, []).append(rule)
    return rule


def delete_rule(tenant_id: str, rule_id: str) -> bool:
    """Tombstone delete; returns True if a matching rule was removed."""
    p = _path()
    with _LOCK:
        store = _load_locked()
        bucket = store.get(tenant_id, [])
        target = next((r for r in bucket if r.id == rule_id), None)
        if target is None:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": rule_id, "tenant_id": tenant_id, "_deleted": True}) + "\n")
        store[tenant_id] = [r for r in bucket if r.id != rule_id]
    return True


def is_allowed(tenant_id: str, client_ip: str) -> bool:
    """Return True when the IP passes the tenant's allowlist.

    Empty rule set = no restriction. Unparseable client IPs are denied
    only when rules exist for the tenant (fail-closed for restricted
    tenants, open for unrestricted ones).
    """
    rules = list_rules(tenant_id)
    if not rules:
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(addr in r.network for r in rules)


def has_rules(tenant_id: str) -> bool:
    return bool(list_rules(tenant_id))


def client_ip_from_request(headers: dict[str, str] | "Iterable[tuple[str, str]]", client_host: str | None) -> str:
    """Extract the originating client IP.

    Honors X-Forwarded-For (first hop) when present, falls back to the
    socket peer. Returns "0.0.0.0" when nothing is known so callers can
    deny safely without raising.
    """
    if isinstance(headers, dict):
        xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or ""
    else:
        xff = ""
        for k, v in headers:
            if k.lower() == "x-forwarded-for":
                xff = v
                break
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return client_host or "0.0.0.0"
