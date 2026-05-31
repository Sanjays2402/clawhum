"""Per-workspace audit log forwarding to a customer-controlled HTTPS sink.

SOC2 CC7.2, ISO 27001 A.12.4, and most enterprise security
questionnaires require that audit events be streamable to the
customer's own SIEM (Splunk, Datadog, Sumo, Panther, generic HTTPS
collector) in near real time. The local hash chained JSONL we already
write covers retention and tamper evidence; this module covers the
"push it to my SIEM" half of the requirement.

Design
------
* One destination per workspace. Stored in a JSONL configuration file
  using the same append only / last writer wins pattern the rest of
  the codebase uses for tenant local config (PATs, IP allowlist,
  webhooks, retention, residency, etc.). A workspace opts in by
  configuring a destination; absence of a record means forwarding is
  off for that workspace.
* Each event is signed with HMAC-SHA256 using a per workspace secret
  shown to the admin exactly once at create / rotate time. The
  receiver verifies ``X-ClawHum-Signature: sha256=<hex>`` against the
  raw request body to authenticate the sender.
* Deliveries are best effort and asynchronous. ``enqueue_event`` is
  called inline by the audit writer; the actual HTTP POST happens on a
  daemon worker thread so a slow sink can never block a tenant
  request. Failed attempts are retried with capped exponential backoff
  up to ``audit_forwarder_max_retries``; after that the attempt is
  recorded as a permanent failure in the delivery log.
* The most recent N delivery attempts per workspace are persisted to
  the delivery log so an admin can see ``status`` (delivered, failed,
  pending), HTTP status, error string, and replay individual events
  through the admin UI.

Strict tenant scoping
---------------------
Every read and write is keyed by ``tenant_id``. The audit middleware
already records ``tenant_id`` per event; we filter on that before
enqueueing so workspace A's events can never be sent to workspace B's
sink, even if both are configured.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from clawhum_core.logging import get_logger
from clawhum_core.settings import get_settings

_log = get_logger("clawhum.audit_forwarder")
_LOCK = Lock()
_CACHE: dict[str, "Destination"] | None = None
_CACHE_PATH: Path | None = None

_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
SECRET_PREFIX = "awsec_"

# Bound the worker queue to prevent unbounded growth if every sink is
# down. We drop oldest events on overflow and record the drop in the
# delivery log so an operator can see the gap.
_MAX_QUEUE = 5_000


def _new_id() -> str:
    return "afw_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LEN))


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(24)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class DestinationError(ValueError):
    """Raised when a destination URL fails validation."""


@dataclass(frozen=True)
class Destination:
    id: str
    tenant_id: str
    url: str
    secret_hash: str
    secret_hint: str
    enabled: bool
    created_at: float
    updated_at: float
    last_attempt_at: float = 0.0
    last_success_at: float = 0.0
    last_status: int = 0
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "url": self.url,
            "secret_hash": self.secret_hash,
            "secret_hint": self.secret_hint,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }


def _path() -> Path:
    return Path(get_settings().audit_forwarder_path)


def _deliveries_path() -> Path:
    return Path(get_settings().audit_forwarder_deliveries_path)


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _validate_url(raw: str) -> str:
    """Reject unsafe destinations. Reuses SSRF policy from webhook_safety
    when available so the same rules apply to SIEM sinks.
    """
    parsed = urlsplit(raw.strip())
    if parsed.scheme not in {"https", "http"}:
        raise DestinationError("url must use http or https")
    if not parsed.hostname:
        raise DestinationError("url must include a host")
    host_lc = parsed.hostname.lower()
    # Belt-and-braces literal denylist: loopback, link local, cloud
    # metadata endpoints, ::1, etc. webhook_safety re-checks after DNS
    # resolution but we want bad literals to fail fast even when the
    # operator has set ``webhook_block_private_ips=false``.
    if host_lc in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.goog",
    } or host_lc.startswith("127."):
        raise DestinationError(
            f"destination {host_lc} is not allowed"
        )
    # Defer in-depth SSRF validation to webhook_safety so the same
    # private-IP/metadata-host rules apply to SIEM sinks as outbound
    # webhooks. Best effort: if the helper is unavailable we still
    # enforce scheme + hostname above.
    # Defer in-depth SSRF validation to webhook_safety so the same
    # private-IP/metadata-host rules apply to SIEM sinks as outbound
    # webhooks. Only do the DNS-resolving check when the operator has
    # private-IP blocking on; otherwise our literal denylist above is
    # the contract.
    try:
        from clawhum_core.settings import get_settings as _gs

        if _gs().webhook_block_private_ips:
            from . import webhook_safety  # local import to avoid cycle

            webhook_safety.validate_destination(
                raw, tenant_id="__audit_forwarder__"
            )
    except Exception as exc:
        # Any safety failure (private IP, metadata host, bad scheme,
        # parser error) becomes a clean 400 for the admin.
        raise DestinationError(str(exc)) from exc
    return raw.strip()


def _load_locked() -> dict[str, Destination]:
    global _CACHE, _CACHE_PATH
    p = _path()
    if _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    out: dict[str, Destination] = {}
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
                tid = row.get("tenant_id")
                if not tid:
                    continue
                if row.get("_deleted"):
                    out.pop(tid, None)
                    continue
                try:
                    out[tid] = Destination(
                        id=str(row["id"]),
                        tenant_id=str(tid),
                        url=str(row["url"]),
                        secret_hash=str(row["secret_hash"]),
                        secret_hint=str(row.get("secret_hint", "")),
                        enabled=bool(row.get("enabled", True)),
                        created_at=float(row.get("created_at") or 0.0),
                        updated_at=float(row.get("updated_at") or 0.0),
                        last_attempt_at=float(row.get("last_attempt_at") or 0.0),
                        last_success_at=float(row.get("last_success_at") or 0.0),
                        last_status=int(row.get("last_status") or 0),
                        last_error=str(row.get("last_error") or ""),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
    _CACHE = out
    _CACHE_PATH = p
    return out


def get_destination(tenant_id: str) -> Destination | None:
    with _LOCK:
        return _load_locked().get(tenant_id)


def _append(rec: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def upsert_destination(tenant_id: str, url: str) -> tuple[Destination, str]:
    """Create or replace this workspace's destination.

    Always rotates the secret; the plain secret is returned exactly
    once and never persisted in clear.
    """
    url_clean = _validate_url(url)
    secret = new_secret()
    now = time.time()
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id)
        rid = existing.id if existing else _new_id()
        created = existing.created_at if existing else now
        dest = Destination(
            id=rid,
            tenant_id=tenant_id,
            url=url_clean,
            secret_hash=hash_secret(secret),
            secret_hint=secret[-4:],
            enabled=True,
            created_at=created,
            updated_at=now,
        )
        _append(dest.to_dict())
        store[tenant_id] = dest
    return dest, secret


def set_enabled(tenant_id: str, enabled: bool) -> Destination | None:
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id)
        if existing is None:
            return None
        updated = Destination(
            id=existing.id,
            tenant_id=tenant_id,
            url=existing.url,
            secret_hash=existing.secret_hash,
            secret_hint=existing.secret_hint,
            enabled=bool(enabled),
            created_at=existing.created_at,
            updated_at=time.time(),
            last_attempt_at=existing.last_attempt_at,
            last_success_at=existing.last_success_at,
            last_status=existing.last_status,
            last_error=existing.last_error,
        )
        _append(updated.to_dict())
        store[tenant_id] = updated
        return updated


def delete_destination(tenant_id: str) -> bool:
    with _LOCK:
        store = _load_locked()
        existing = store.pop(tenant_id, None)
        if existing is None:
            return False
        _append({"tenant_id": tenant_id, "id": existing.id, "_deleted": True})
        return True


def _update_status(
    tenant_id: str,
    *,
    attempt_at: float,
    success_at: float | None,
    status_code: int,
    error: str,
) -> None:
    with _LOCK:
        store = _load_locked()
        existing = store.get(tenant_id)
        if existing is None:
            return
        updated = Destination(
            id=existing.id,
            tenant_id=tenant_id,
            url=existing.url,
            secret_hash=existing.secret_hash,
            secret_hint=existing.secret_hint,
            enabled=existing.enabled,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
            last_attempt_at=attempt_at,
            last_success_at=success_at if success_at is not None else existing.last_success_at,
            last_status=status_code,
            last_error=error[:240],
        )
        _append(updated.to_dict())
        store[tenant_id] = updated


# ---- Delivery log ---------------------------------------------------

@dataclass
class DeliveryRecord:
    delivery_id: str
    tenant_id: str
    destination_id: str
    event_ts: float
    attempt: int
    status: str  # delivered | failed | dropped
    http_status: int
    error: str
    duration_ms: float
    request_id: str | None
    event_path: str
    event_method: str
    event_actor: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


_DELIV_LOCK = Lock()


def _delivery_records_locked() -> list[dict[str, Any]]:
    p = _deliveries_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def list_deliveries(tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with _DELIV_LOCK:
        rows = [r for r in _delivery_records_locked() if r.get("tenant_id") == tenant_id]
    rows.sort(key=lambda r: r.get("event_ts") or 0.0, reverse=True)
    return rows[:limit]


def _record_delivery(rec: DeliveryRecord) -> None:
    keep = max(50, int(get_settings().audit_forwarder_delivery_log_keep))
    p = _deliveries_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _DELIV_LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict()) + "\n")
        # Compact: keep most recent ``keep`` rows per tenant. Cheap
        # because the cap is small (~200) and the file is bounded.
        rows = _delivery_records_locked()
        by_tenant: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_tenant.setdefault(str(r.get("tenant_id") or ""), []).append(r)
        compacted: list[dict[str, Any]] = []
        for tid, bucket in by_tenant.items():
            bucket.sort(key=lambda r: r.get("event_ts") or 0.0, reverse=True)
            compacted.extend(reversed(bucket[:keep]))
        # Rewrite the file atomically.
        tmp = p.with_suffix(p.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in compacted:
                fh.write(json.dumps(r) + "\n")
        tmp.replace(p)


# ---- Signing + transport -------------------------------------------

def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - URL validated upstream
            return int(resp.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.reason or "http error"
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)
    except Exception as exc:  # pragma: no cover - defensive
        return 0, str(exc)


def _send_once(
    dest: Destination,
    secret: str,
    event: dict[str, Any],
    *,
    timeout: float,
    request_id: str | None = None,
) -> tuple[int, str, float]:
    body = json.dumps(
        {"workspace": dest.tenant_id, "event": event},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "clawhum-audit-forwarder/1",
        "X-ClawHum-Workspace": dest.tenant_id,
        "X-ClawHum-Destination": dest.id,
        "X-ClawHum-Signature": sign_payload(secret, body),
        "X-ClawHum-Event-Ts": str(event.get("ts", "")),
    }
    if request_id:
        headers["X-Request-ID"] = request_id
    started = time.time()
    code, err = _post(dest.url, body, headers, timeout)
    return code, err, (time.time() - started) * 1000.0


def deliver_with_test_secret(
    dest: Destination,
    plain_secret: str,
    event: dict[str, Any],
) -> tuple[int, str, float]:
    """Synchronous delivery used by the /test endpoint at create time
    so the admin can verify their sink before relying on async fan out.
    """
    return _send_once(
        dest,
        plain_secret,
        event,
        timeout=get_settings().audit_forwarder_timeout_seconds,
    )


# ---- Async worker --------------------------------------------------

@dataclass
class _Job:
    tenant_id: str
    event: dict[str, Any]
    attempt: int = 0
    next_at: float = 0.0


class _Worker:
    def __init__(self) -> None:
        self.queue: deque[_Job] = deque()
        self.cv = threading.Condition()
        self.thread: threading.Thread | None = None
        # Test hook so unit tests can intercept HTTP without sockets.
        self.send_hook: Callable[[Destination, dict[str, Any]], tuple[int, str, float]] | None = None
        # Secret cache so the worker can sign without holding the
        # plaintext in the destination record. The plaintext is set
        # once at upsert/rotate time and stays in process memory only.
        self._secrets: dict[str, str] = {}
        self._secrets_lock = Lock()

    def register_secret(self, tenant_id: str, secret: str) -> None:
        with self._secrets_lock:
            self._secrets[tenant_id] = secret

    def secret_for(self, tenant_id: str) -> str | None:
        with self._secrets_lock:
            return self._secrets.get(tenant_id)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        t = threading.Thread(
            target=self._run, name="audit-forwarder", daemon=True
        )
        self.thread = t
        t.start()

    def enqueue(self, job: _Job) -> bool:
        with self.cv:
            if len(self.queue) >= _MAX_QUEUE:
                # Drop oldest; record the drop synchronously so admins
                # can see the gap when they review the delivery log.
                dropped = self.queue.popleft()
                try:
                    _record_delivery(
                        DeliveryRecord(
                            delivery_id=_new_id(),
                            tenant_id=dropped.tenant_id,
                            destination_id="",
                            event_ts=float(dropped.event.get("ts") or 0.0),
                            attempt=dropped.attempt,
                            status="dropped",
                            http_status=0,
                            error="queue overflow",
                            duration_ms=0.0,
                            request_id=dropped.event.get("request_id"),
                            event_path=str(dropped.event.get("path") or ""),
                            event_method=str(dropped.event.get("method") or ""),
                            event_actor=str(dropped.event.get("actor") or ""),
                        )
                    )
                except Exception:  # pragma: no cover - defensive
                    pass
            self.queue.append(job)
            self.cv.notify()
            return True

    def _run(self) -> None:  # pragma: no cover - exercised via drain_for_tests
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait(timeout=1.0)
                now = time.time()
                # Find the next job whose ``next_at`` has elapsed.
                job = None
                for i, candidate in enumerate(self.queue):
                    if candidate.next_at <= now:
                        del self.queue[i]
                        job = candidate
                        break
                if job is None:
                    self.cv.wait(timeout=0.5)
                    continue
            self._process(job)

    def _process(self, job: _Job) -> None:
        settings = get_settings()
        dest = get_destination(job.tenant_id)
        if dest is None or not dest.enabled:
            return
        secret = self.secret_for(job.tenant_id)
        if not secret:
            # No plaintext in this process. Persist a failure so admins
            # know to rotate; otherwise events would silently vanish.
            _record_delivery(
                DeliveryRecord(
                    delivery_id=_new_id(),
                    tenant_id=job.tenant_id,
                    destination_id=dest.id,
                    event_ts=float(job.event.get("ts") or 0.0),
                    attempt=job.attempt + 1,
                    status="failed",
                    http_status=0,
                    error="no signing secret in process (rotate to re-enable)",
                    duration_ms=0.0,
                    request_id=job.event.get("request_id"),
                    event_path=str(job.event.get("path") or ""),
                    event_method=str(job.event.get("method") or ""),
                    event_actor=str(job.event.get("actor") or ""),
                )
            )
            return

        attempt = job.attempt + 1
        if self.send_hook is not None:
            code, err, dur = self.send_hook(dest, job.event)
        else:
            code, err, dur = _send_once(
                dest,
                secret,
                job.event,
                timeout=settings.audit_forwarder_timeout_seconds,
                request_id=job.event.get("request_id"),
            )
        delivered = 200 <= code < 300
        now = time.time()
        _update_status(
            job.tenant_id,
            attempt_at=now,
            success_at=now if delivered else None,
            status_code=code,
            error="" if delivered else (err or f"http {code}"),
        )
        if delivered:
            _record_delivery(
                DeliveryRecord(
                    delivery_id=_new_id(),
                    tenant_id=job.tenant_id,
                    destination_id=dest.id,
                    event_ts=float(job.event.get("ts") or 0.0),
                    attempt=attempt,
                    status="delivered",
                    http_status=code,
                    error="",
                    duration_ms=dur,
                    request_id=job.event.get("request_id"),
                    event_path=str(job.event.get("path") or ""),
                    event_method=str(job.event.get("method") or ""),
                    event_actor=str(job.event.get("actor") or ""),
                )
            )
            return

        if attempt >= settings.audit_forwarder_max_retries:
            _record_delivery(
                DeliveryRecord(
                    delivery_id=_new_id(),
                    tenant_id=job.tenant_id,
                    destination_id=dest.id,
                    event_ts=float(job.event.get("ts") or 0.0),
                    attempt=attempt,
                    status="failed",
                    http_status=code,
                    error=err or f"http {code}",
                    duration_ms=dur,
                    request_id=job.event.get("request_id"),
                    event_path=str(job.event.get("path") or ""),
                    event_method=str(job.event.get("method") or ""),
                    event_actor=str(job.event.get("actor") or ""),
                )
            )
            return
        # Backoff: 1s, 2s, 4s, 8s, capped at 30s.
        delay = min(30.0, 2.0 ** (attempt - 1))
        with self.cv:
            self.queue.append(
                _Job(
                    tenant_id=job.tenant_id,
                    event=job.event,
                    attempt=attempt,
                    next_at=time.time() + delay,
                )
            )
            self.cv.notify()

    def drain_for_tests(self, max_iterations: int = 50) -> None:
        """Synchronously process pending jobs whose ``next_at`` is due.

        Tests register a ``send_hook`` and then call this to avoid
        sleeping on real exponential backoff. Jobs scheduled for the
        future are left alone; bump ``time.time`` or call repeatedly to
        flush retries.
        """
        for _ in range(max_iterations):
            with self.cv:
                now = time.time()
                idx = None
                for i, candidate in enumerate(self.queue):
                    if candidate.next_at <= now:
                        idx = i
                        break
                if idx is None:
                    return
                job = self.queue[idx]
                del self.queue[idx]
            self._process(job)


_WORKER = _Worker()


def get_worker() -> _Worker:
    return _WORKER


def enqueue_event(event: dict[str, Any]) -> None:
    """Inline-fast hook called by the audit writer for each event.

    Drops silently when forwarding is globally off, the event has no
    tenant id, or the tenant has no destination configured. Never
    raises so a misconfigured sink cannot break the request path.
    """
    try:
        if not get_settings().audit_forwarder_enabled:
            return
        tid = event.get("tenant_id")
        if not tid:
            return
        dest = get_destination(str(tid))
        if dest is None or not dest.enabled:
            return
        _WORKER.start()
        _WORKER.enqueue(_Job(tenant_id=str(tid), event=dict(event)))
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("audit_forwarder_enqueue_failed", error=str(exc))


def replay_event(tenant_id: str, event: dict[str, Any]) -> None:
    """Re-enqueue an event for delivery. Used by the admin replay UI.

    Caller is responsible for ensuring ``event['tenant_id']`` matches
    ``tenant_id`` so cross workspace replay is impossible.
    """
    event = dict(event)
    event["tenant_id"] = tenant_id
    enqueue_event(event)


def verify_signature(secret: str, body: bytes, header_value: str) -> bool:
    """Constant time HMAC verification helper for receivers and tests."""
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected.encode("ascii"), header_value.encode("ascii"))
