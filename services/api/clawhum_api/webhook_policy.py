"""Per-workspace transport policy for outbound webhooks.

Why this exists
---------------
``webhook_safety`` already blocks SSRF targets (private ranges, cloud
metadata) at both registration time and delivery time. What it does
NOT do is force the transport scheme or the negotiated TLS version.
The global default still allows ``http://`` receivers because some
on-prem deployments terminate TLS at a load balancer one hop away.
That default fails enterprise procurement: SOC2 CC6.7 and most DPAs
require that webhook payloads (which carry signed records of customer
data and an HMAC secret in every header) only ever cross TLS, and most
require TLS 1.2 or newer.

Each workspace can flip two knobs:

* ``require_https``: while on, ``validate_destination`` rejects
  plaintext URLs at create time (HTTP 400) and at delivery time
  (recorded as a policy block in the delivery log, never sent).
* ``min_tls_version``: ``""`` (no floor), ``"1.2"``, or ``"1.3"``.
  While set, the outbound httpx client is built with an SSLContext
  whose ``minimum_version`` matches; receivers that cannot negotiate
  the floor fail the handshake and the delivery is logged as a
  TLS policy error without ever sending the payload. Setting any TLS
  floor implicitly also requires https since plaintext has no TLS to
  pin.

The policy is strictly per tenant; tenant A turning enforcement on
has zero effect on tenant B. Storage follows the same append-only
JSONL last-writer-wins pattern as ``scope_policy``/``invite_domains``
/``ip_allowlist`` so no new infra is needed and multi-worker writers
stay safe.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings

_LOCK = Lock()
_CACHE: dict[str, "Policy"] | None = None
_CACHE_PATH: Path | None = None


ALLOWED_TLS_VERSIONS: frozenset[str] = frozenset({"", "1.2", "1.3"})


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    require_https: bool
    min_tls_version: str  # "", "1.2", or "1.3"
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "require_https": self.require_https,
            "min_tls_version": self.min_tls_version,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    s = get_settings()
    # Reuse a dedicated path; default lives next to the other jsonl stores.
    p = getattr(s, "webhook_policy_path", None)
    if p is None:
        p = Path(getattr(s, "webhooks_path", Path("./data/webhooks.jsonl"))).parent / "webhook_policy.jsonl"
    return Path(p)


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
                raw_tls = str(row.get("min_tls_version") or "").strip()
                if raw_tls not in ALLOWED_TLS_VERSIONS:
                    raw_tls = ""
                out[tid] = Policy(
                    tenant_id=tid,
                    require_https=bool(row.get("require_https", False)),
                    min_tls_version=raw_tls,
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


def get_policy(tenant_id: str) -> Policy:
    with _LOCK:
        pol = _load_locked().get(tenant_id)
    if pol is None:
        return Policy(tenant_id=tenant_id, require_https=False,
                      min_tls_version="",
                      updated_at=0.0, updated_by="")
    return pol


def require_https(tenant_id: str) -> bool:
    pol = get_policy(tenant_id)
    # Pinning a TLS floor only makes sense over TLS, so it implies https.
    return pol.require_https or bool(pol.min_tls_version)


def min_tls_version(tenant_id: str) -> str:
    """Return ``""``, ``"1.2"``, or ``"1.3"`` for the workspace."""
    return get_policy(tenant_id).min_tls_version


def set_policy(
    *,
    tenant_id: str,
    require_https: bool,
    updated_by: str,
    min_tls_version: str | None = None,
) -> Policy:
    current = get_policy(tenant_id)
    raw_tls = (min_tls_version if min_tls_version is not None
               else current.min_tls_version)
    raw_tls = (raw_tls or "").strip()
    if raw_tls not in ALLOWED_TLS_VERSIONS:
        raise ValueError(
            f"min_tls_version must be one of {sorted(ALLOWED_TLS_VERSIONS)!r}"
        )
    row = Policy(
        tenant_id=tenant_id,
        require_https=bool(require_https),
        min_tls_version=raw_tls,
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


class HttpsRequiredError(ValueError):
    """Raised when a webhook URL is plaintext but the workspace forbids it."""

    code = "webhook_https_required"

    def __init__(self, host: str = ""):
        self.host = host
        super().__init__(
            "workspace policy requires https for webhook destinations"
        )


class TlsVersionError(ValueError):
    """Raised when a delivery cannot satisfy the workspace TLS floor."""

    code = "webhook_tls_version_required"

    def __init__(self, floor: str, host: str = "", reason: str = ""):
        self.floor = floor
        self.host = host
        self.reason = reason
        super().__init__(
            f"workspace policy requires TLS {floor} or newer for webhook "
            f"destinations"
        )


def build_ssl_context(min_tls: str):
    """Return an SSLContext pinned to ``min_tls`` (``"1.2"``/``"1.3"``).

    Returns ``None`` when no floor is configured so callers fall back to
    httpx defaults. Keeps verification on; this only raises the floor.
    """
    if not min_tls:
        return None
    import ssl

    ctx = ssl.create_default_context()
    if min_tls == "1.3":
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    elif min_tls == "1.2":
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    else:
        raise ValueError(f"unsupported min_tls value: {min_tls!r}")
    return ctx
