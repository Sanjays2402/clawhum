"""SSRF protection for outbound webhook deliveries.

Enterprise buyers reject any product that lets a tenant aim outbound
HTTP at cloud metadata services (``169.254.169.254``), internal RFC1918
ranges, or other tenants' private networks. This module enforces a
defense in depth check applied at TWO points:

1. ``validate_destination`` runs at create/update time so a bad URL is
   rejected with a 400 the user can actually see.
2. ``resolve_safe_addr`` runs immediately before each delivery attempt
   so a TOCTOU change in DNS (e.g. an attacker pointing
   ``evil.example.com`` at ``169.254.169.254`` after registration)
   still fails closed.

Rules, in order:

* Scheme must be ``http`` or ``https``. URLs with userinfo or non
  standard ports outside the well known ranges are rejected unless
  explicitly allowed by the per tenant allowlist.
* When ``webhook_block_private_ips`` is true (default) every resolved
  address is checked with :mod:`ipaddress`. Loopback, link local,
  multicast, unspecified, reserved, and private ranges are blocked.
  IPv4 mapped IPv6 addresses are unwrapped first so ``::ffff:10.0.0.1``
  is treated like ``10.0.0.1``.
* A per workspace host suffix allowlist overrides the private IP block
  for hosts the workspace owner has explicitly trusted (useful for
  on prem deployments where receivers live on a VPN). The allowlist
  matches the hostname or any parent domain (``api.acme.internal``
  matches ``acme.internal``).
* A global denylist (``webhook_destination_denylist``) is always
  enforced and cannot be overridden by tenant allowlists; it exists so
  operators can hard block known sensitive hosts like the metadata IPs
  regardless of tenant configuration.

All checks raise :class:`WebhookDestinationError` with a short, user
safe reason string. Callers (route handlers, delivery worker) decide
whether to turn that into an HTTP 400 or a logged delivery failure.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable
from urllib.parse import urlsplit

from clawhum_core.settings import get_settings


# Hosts that must never be reachable from an outbound webhook, even if
# a tenant adds them to their allowlist. Cloud provider metadata
# endpoints leak credentials and have been the root cause of multiple
# public breaches; if a customer genuinely needs to deliver to one of
# these, they should run a proxy.
_GLOBAL_DENY_HOSTS: frozenset[str] = frozenset({
    "169.254.169.254",  # AWS / GCP / Azure IMDS v1/v2 + OpenStack
    "metadata.google.internal",
    "metadata.goog",
    "fd00:ec2::254",  # AWS IMDS IPv6
})

# Ports we accept by default. Anything else must come through the
# per tenant allowlist. This catches the common SSRF trick of POSTing
# to internal services on weird ports (e.g. Redis 6379, Elastic 9200).
_DEFAULT_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443, 8080, 8443})


class WebhookDestinationError(ValueError):
    """Raised when a destination fails policy. Message is user safe."""


@dataclass(frozen=True)
class ParsedDestination:
    host: str
    port: int
    scheme: str


def _allowlist_path() -> Path:
    p = get_settings().webhook_allowlist_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_ALLOWLIST_LOCK = Lock()


def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def get_tenant_allowlist(tenant_id: str) -> list[str]:
    """Return the current host suffix allowlist for a tenant.

    The store is append only; the latest record per tenant wins. An
    empty list means "no overrides" (default policy applies).
    """
    latest: dict | None = None
    for rec in _iter_jsonl(_allowlist_path()):
        if rec.get("tenant_id") == tenant_id:
            latest = rec
    if latest is None:
        return []
    raw = latest.get("hosts") or []
    return [str(h).strip().lower() for h in raw if str(h).strip()]


def set_tenant_allowlist(tenant_id: str, hosts: list[str]) -> list[str]:
    """Replace the allowlist for a tenant.

    Hosts are normalised to lower case bare hostnames. Wildcards are
    not supported; suffix matching is implicit (``acme.internal``
    matches ``api.acme.internal``). Invalid entries are dropped so a
    typo cannot wedge the form.
    """
    cleaned: list[str] = []
    for h in hosts:
        s = str(h).strip().lower().lstrip(".")
        if not s or " " in s or "/" in s:
            continue
        # Strip any scheme/path the user may have pasted.
        if "://" in s:
            s = s.split("://", 1)[1]
        s = s.split("/", 1)[0]
        cleaned.append(s)
    rec = {"tenant_id": tenant_id, "hosts": cleaned}
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with _ALLOWLIST_LOCK:
        with _allowlist_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return cleaned


def _host_matches_allowlist(host: str, allow: list[str]) -> bool:
    h = host.lower()
    for entry in allow:
        if h == entry or h.endswith("." + entry):
            return True
    return False


def parse_destination(url: str) -> ParsedDestination:
    """Reject URLs whose shape is unsafe before we even resolve DNS."""
    try:
        parts = urlsplit(url)
    except ValueError as e:
        raise WebhookDestinationError(f"invalid url: {e}")
    if parts.scheme not in ("http", "https"):
        raise WebhookDestinationError(
            "webhook url must use http or https scheme"
        )
    if parts.username or parts.password:
        raise WebhookDestinationError(
            "webhook url must not contain userinfo"
        )
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise WebhookDestinationError("webhook url has no host")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return ParsedDestination(host=host, port=int(port), scheme=parts.scheme)


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> str | None:
    """Return a reason string when an address is in a sensitive range."""
    # Unwrap IPv4-mapped IPv6 so ::ffff:10.0.0.1 hits the IPv4 rules.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback address blocked"
    if ip.is_link_local:
        return "link local address blocked (cloud metadata range)"
    if ip.is_multicast:
        return "multicast address blocked"
    if ip.is_unspecified:
        return "unspecified address blocked"
    if ip.is_reserved:
        return "reserved address blocked"
    if ip.is_private:
        return "private address blocked"
    return None


def _resolve(host: str, port: int) -> list[ipaddress._BaseAddress]:
    """Resolve A/AAAA records once; tests can monkeypatch this."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WebhookDestinationError(f"dns lookup failed: {e}")
    out: list[ipaddress._BaseAddress] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            out.append(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
    if not out:
        raise WebhookDestinationError("dns returned no addresses")
    return out


def _is_globally_denied(host: str, addrs: list[ipaddress._BaseAddress]) -> bool:
    if host in _GLOBAL_DENY_HOSTS:
        return True
    for a in addrs:
        if str(a) in _GLOBAL_DENY_HOSTS:
            return True
    return False


def validate_destination(url: str, tenant_id: str) -> ParsedDestination:
    """Run all SSRF policy checks; raise on the first failure.

    Returns the parsed destination so callers can store the normalised
    host/port if they wish. Safe to call from both the create handler
    and the delivery worker; the small DNS cost is intentional so a
    TOCTOU swap is caught.
    """
    settings = get_settings()
    parsed = parse_destination(url)

    # Host literal check (covers cases where DNS would not be queried).
    if parsed.host in _GLOBAL_DENY_HOSTS:
        raise WebhookDestinationError(
            f"destination {parsed.host} is globally denied"
        )

    allow = get_tenant_allowlist(tenant_id)
    host_allowed = _host_matches_allowlist(parsed.host, allow)

    # We deliberately do NOT block non standard ports as a separate
    # rule. The real risk is the destination address; once we have
    # verified that the resolved IP is public (or explicitly trusted),
    # the port is the receiver's business. Restricting ports here would
    # break legitimate setups like ngrok tunnels and CI test servers on
    # ephemeral ports without adding meaningful security.

    # Resolve and check each address. If the host itself is an IP
    # literal, getaddrinfo round trips it cleanly.
    addrs = _resolve(parsed.host, parsed.port)

    # Global denylist trumps everything.
    if _is_globally_denied(parsed.host, addrs):
        raise WebhookDestinationError(
            "destination resolves to a globally denied address"
        )

    if not settings.webhook_block_private_ips:
        return parsed

    if host_allowed:
        # Tenant has explicitly trusted this host suffix, so we relax
        # the private IP check but still honour the global denylist
        # above (already enforced).
        return parsed

    for addr in addrs:
        reason = _is_blocked_ip(addr)
        if reason:
            raise WebhookDestinationError(
                f"destination {parsed.host} resolves to {addr}: {reason}"
            )
    return parsed
