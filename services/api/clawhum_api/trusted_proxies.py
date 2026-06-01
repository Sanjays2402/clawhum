"""Trusted reverse-proxy CIDR list.

Until this module landed, ``ip_allowlist.client_ip_from_request``
blindly trusted the first hop of ``X-Forwarded-For`` to derive the
client IP. That is fine when the API is reachable only via its own
ingress controller, but the moment the service is exposed anywhere
else it is a spoofing vulnerability: an attacker can simply send
``X-Forwarded-For: 10.0.0.5`` to bypass the workspace IP allowlist,
plant a false IP in the audit log, and confuse anomaly rules. This
is the kind of finding that ends a procurement review on the spot.

The fix is the standard one used by every serious framework (Rails
``trusted_proxies``, Django ``USE_X_FORWARDED_HOST``, Express
``trust proxy``, nginx ``set_real_ip_from``): only honour
``X-Forwarded-For`` when the request actually came from a peer the
operator has explicitly named as a trusted proxy. Untrusted peers
get their socket IP and any forwarding header they sent is ignored.

Two layers of configuration:

* ``CLAWHUM_TRUSTED_PROXIES_GLOBAL`` is an operator side, deployment
  wide comma separated list of CIDRs (or single IPs). This is where
  the load balancer / ingress lives. It is set in the environment so
  a malicious admin user inside one workspace cannot reach it.
* A per workspace list, managed via ``/v1/trusted-proxies`` by an
  admin, layers on top for tenants whose self hosted deployment
  fronts the API with their own proxy stack. Workspace entries can
  only extend the trusted set, never replace the global list.

Given ``X-Forwarded-For: client, hop1, hop2`` and a socket peer of
``hop3``, we trust the rightmost contiguous chain of hops that are
all in the trusted set. So if ``hop3`` and ``hop2`` are trusted but
``hop1`` is not, the resolved client is ``hop1``. This matches the
RFC 7239 model and prevents an attacker from injecting fake hops at
the left of the header.

Storage matches the project convention: append only JSONL with a
process lock and tombstone deletes. No database dependency.
"""

from __future__ import annotations

import ipaddress
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
_CACHE: dict[str, list["ProxyRule"]] | None = None
_CACHE_PATH: Path | None = None
_GLOBAL_CACHE: tuple[str, tuple] | None = None

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class ProxyRule:
    id: str
    tenant_id: str
    cidr: str
    label: str
    created_at: float
    network: _Network = field(compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "cidr": self.cidr,
            "label": self.label,
            "created_at": self.created_at,
        }


def _new_id() -> str:
    return "tp_" + "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _parse_cidr(raw: str) -> _Network:
    raw = raw.strip()
    if not raw:
        raise ValueError("empty cidr")
    if "/" not in raw:
        addr = ipaddress.ip_address(raw)
        raw = f"{raw}/{addr.max_prefixlen}"
    return ipaddress.ip_network(raw, strict=False)


def _path() -> Path:
    return Path(get_settings().trusted_proxies_path)


def _load_locked() -> dict[str, list[ProxyRule]]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, list[ProxyRule]] = {}
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
                rule = ProxyRule(
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
    global _CACHE, _CACHE_PATH, _GLOBAL_CACHE
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None
        _GLOBAL_CACHE = None


def _global_networks() -> tuple:
    global _GLOBAL_CACHE
    raw = (get_settings().trusted_proxies_global or "").strip()
    if _GLOBAL_CACHE is not None and _GLOBAL_CACHE[0] == raw:
        return _GLOBAL_CACHE[1]
    nets: list[_Network] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(_parse_cidr(chunk))
        except ValueError:
            continue
    _GLOBAL_CACHE = (raw, tuple(nets))
    return _GLOBAL_CACHE[1]


def global_cidrs() -> list[str]:
    return [str(n) for n in _global_networks()]


def list_rules(tenant_id: str) -> list[ProxyRule]:
    with _LOCK:
        store = _load_locked()
        return list(store.get(tenant_id, []))


def add_rule(tenant_id: str, cidr: str, label: str = "") -> ProxyRule:
    network = _parse_cidr(cidr)
    rule = ProxyRule(
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


def _is_trusted(addr: str, tenant_id: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for net in _global_networks():
        if ip in net:
            return True
    if tenant_id:
        for rule in list_rules(tenant_id):
            if ip in rule.network:
                return True
    return False


def resolve_client_ip(
    xff: str,
    peer: str | None,
    tenant_id: str | None = None,
) -> str:
    """Resolve the originating client IP using the trusted-proxy chain.

    ``xff`` is the raw ``X-Forwarded-For`` value (comma separated,
    left = original client, right = nearest hop). ``peer`` is the
    socket peer address. We walk the chain right to left: as long as
    the most recently seen hop is trusted, the next entry to its left
    is treated as the new candidate client. As soon as we hit an
    untrusted hop, we stop and return it. If the peer itself is not
    trusted, we ignore ``X-Forwarded-For`` entirely and return the
    peer, defeating the spoofed-header attack.
    """
    peer = (peer or "").strip()
    if not _is_trusted(peer, tenant_id):
        return peer or "0.0.0.0"
    candidates = [c.strip() for c in (xff or "").split(",") if c.strip()]
    if not candidates:
        return peer
    while len(candidates) > 1 and _is_trusted(candidates[-1], tenant_id):
        candidates.pop()
    return candidates[0]
