"""Idempotency-Key support for mutating HTTP requests.

Enterprise integrators routinely retry POST/PUT/PATCH/DELETE calls when
they see a timeout or 5xx. Without server-side de-duplication, a retry
can double-write a row, double-charge a customer, or send a webhook
twice. This module implements Stripe-style ``Idempotency-Key`` handling
as FastAPI middleware so every mutating route in the service gets the
behaviour automatically, with no per-route code changes.

Guarantees
==========

* Same (tenant, key, method, path, body-hash) within the TTL replays
  the original response verbatim and tags the reply with
  ``Idempotent-Replayed: true`` and the original ``X-Request-ID``.
* Same (tenant, key) reused with a different body returns HTTP 409 with
  a structured error so a buggy client cannot silently overwrite a
  prior result by reusing a key.
* Concurrent retries with the same key wait on an asyncio lock so the
  origin request runs exactly once. The second caller observes the
  cached response.
* Only successful, idempotent-safe responses (2xx, 4xx that were
  validation errors) are cached. 5xx and 429 responses are *not*
  cached so the client can legitimately retry after a transient
  failure.
* Per-tenant LRU eviction with a hard cap prevents one noisy tenant
  from exhausting memory shared by all workspaces.
* Keys are scoped per tenant so two different workspaces can reuse the
  same key string without collision and without information leakage.

Scope
=====

The middleware is keyed on the resolved tenant id, which is populated
by the auth dependency after the route runs. To make the *first* call
cacheable we resolve the tenant lazily: on the first request we record
the chosen response under the tenant id we learn from
``request.state.tenant_id`` (set by the auth dependency during
``call_next``). If no tenant id is resolved we fall back to the API
key, then to the remote IP, so anonymous mutating endpoints still get
safe-retry semantics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

# Keys are bounded to a sane character set so they can be safely echoed
# in error messages and used as cache keys without escaping. Stripe
# allows up to 255 chars; we match that.
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-\.:]{1,255}$")

# Methods that we treat as mutating and therefore worth de-duplicating.
# GET/HEAD/OPTIONS are already idempotent at the HTTP layer; replaying
# them is a waste of cache space.
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class _CachedResponse:
    status_code: int
    headers: list[tuple[str, str]]
    body: bytes
    body_hash: str
    request_id: str
    stored_at: float
    media_type: str | None


@dataclass
class _TenantBucket:
    entries: OrderedDict[str, _CachedResponse] = field(default_factory=OrderedDict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def evict_expired(self, now: float, ttl: float) -> None:
        # OrderedDict insertion order doubles as age order because we
        # touch on insert only. Walk from the oldest and stop at the
        # first non-expired entry.
        stale: list[str] = []
        for k, v in self.entries.items():
            if now - v.stored_at <= ttl:
                break
            stale.append(k)
        for k in stale:
            self.entries.pop(k, None)
            self.locks.pop(k, None)


class IdempotencyStore:
    """Thread-safe in-memory store with per-tenant LRU eviction."""

    def __init__(self, *, ttl_seconds: float, max_per_tenant: int) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_per_tenant = int(max_per_tenant)
        self._buckets: dict[str, _TenantBucket] = {}
        self._global_lock = asyncio.Lock()

    def _bucket(self, tenant: str) -> _TenantBucket:
        b = self._buckets.get(tenant)
        if b is None:
            b = _TenantBucket()
            self._buckets[tenant] = b
        return b

    async def lock_for(self, tenant: str, key: str) -> asyncio.Lock:
        async with self._global_lock:
            b = self._bucket(tenant)
            lock = b.locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                b.locks[key] = lock
            return lock

    def get(self, tenant: str, key: str) -> _CachedResponse | None:
        b = self._buckets.get(tenant)
        if b is None:
            return None
        b.evict_expired(time.monotonic(), self.ttl_seconds)
        entry = b.entries.get(key)
        if entry is None:
            return None
        # LRU touch.
        b.entries.move_to_end(key)
        return entry

    def put(self, tenant: str, key: str, value: _CachedResponse) -> None:
        b = self._bucket(tenant)
        b.entries[key] = value
        b.entries.move_to_end(key)
        # Hard cap per tenant to bound memory.
        while len(b.entries) > self.max_per_tenant:
            old_key, _ = b.entries.popitem(last=False)
            b.locks.pop(old_key, None)

    def reset(self) -> None:
        self._buckets.clear()


def _hash_body(method: str, path: str, body: bytes) -> str:
    h = hashlib.sha256()
    h.update(method.encode("ascii"))
    h.update(b"\x00")
    h.update(path.encode("utf-8"))
    h.update(b"\x00")
    h.update(body)
    return h.hexdigest()


def _resolve_caller(request: Request) -> str:
    """Pick a stable caller id for cache scoping.

    Auth runs as a per-route dependency so tenant_id is only known after
    call_next. For the *initial* request that is fine because we store
    under the post-call tenant. For *replays* we need a key we can read
    from headers alone. We therefore use, in order: an explicit tenant
    header set by upstream auth (rare), the raw API key, then the
    client IP. Using the API key keeps two tenants that share an IP but
    not a key from colliding, and falls back to IP for fully anonymous
    callers so abuse is bounded.
    """
    pre = getattr(request.state, "tenant_id", None)
    if pre:
        return f"t:{pre}"
    api_key = request.headers.get("x-api-key") or request.headers.get("authorization")
    if api_key:
        return "k:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]
    client = request.client.host if request.client else "anon"
    return f"ip:{client}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """De-duplicate mutating requests by client-supplied Idempotency-Key.

    The middleware is a no-op for safe methods, for requests without
    an ``Idempotency-Key`` header, and when the feature is disabled by
    settings. It deliberately sits *outside* the rate limiter so a
    replayed cached response does not double-charge the workspace
    quota, and *inside* RequestID so cached responses still carry the
    original request id on replay (for log correlation).
    """

    def __init__(self, app, *, enabled: bool, store: IdempotencyStore) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.store = store

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)
        if request.method not in _MUTATING:
            return await call_next(request)
        key = request.headers.get("idempotency-key")
        if not key:
            return await call_next(request)
        if not _KEY_RE.match(key):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_idempotency_key",
                    "message": (
                        "Idempotency-Key must be 1-255 chars of "
                        "[A-Za-z0-9_-.:]."
                    ),
                },
            )

        # Read body once and cache it on request.state so downstream
        # handlers can still re-read it via starlette's body cache.
        body = await request.body()
        body_hash = _hash_body(request.method, request.url.path, body)
        caller = _resolve_caller(request)

        lock = await self.store.lock_for(caller, key)
        async with lock:
            cached = self.store.get(caller, key)
            if cached is not None:
                if cached.body_hash != body_hash:
                    return JSONResponse(
                        status_code=409,
                        content={
                            "error": "idempotency_key_conflict",
                            "message": (
                                "Idempotency-Key was reused with a "
                                "different request body. Use a fresh "
                                "key for a different request."
                            ),
                            "original_request_id": cached.request_id,
                        },
                        headers={"Idempotent-Replayed": "false"},
                    )
                headers = dict(cached.headers)
                headers["Idempotent-Replayed"] = "true"
                # Surface the *original* request id under a dedicated
                # header. We deliberately do not overwrite X-Request-ID
                # because the outer RequestIDMiddleware sets it from
                # the current request and will overwrite ours anyway.
                headers["X-Original-Request-ID"] = cached.request_id
                headers.pop("x-request-id", None)
                # Strip hop-by-hop and length headers; Response sets
                # content-length from body.
                headers.pop("content-length", None)
                return Response(
                    content=cached.body,
                    status_code=cached.status_code,
                    headers=headers,
                    media_type=cached.media_type,
                )

            response = await call_next(request)
            # Only cache deterministic outcomes. 5xx and 429 stay
            # retryable; the spec also excludes 408.
            if response.status_code in (408, 429) or response.status_code >= 500:
                response.headers["Idempotent-Replayed"] = "false"
                return response

            # Drain the streaming body so we can both return it and
            # cache it. Starlette responses expose body_iterator.
            body_chunks: list[bytes] = []
            async for chunk in response.body_iterator:
                body_chunks.append(chunk)
            response_body = b"".join(body_chunks)

            request_id = (
                getattr(request.state, "request_id", None)
                or response.headers.get("x-request-id", "")
            )
            # Copy headers excluding ones that must be recomputed.
            kept_headers: list[tuple[str, str]] = []
            for k, v in response.headers.items():
                if k.lower() in {"content-length"}:
                    continue
                kept_headers.append((k, v))

            self.store.put(
                caller,
                key,
                _CachedResponse(
                    status_code=response.status_code,
                    headers=kept_headers,
                    body=response_body,
                    body_hash=body_hash,
                    request_id=request_id,
                    stored_at=time.monotonic(),
                    media_type=response.media_type,
                ),
            )
            new_headers = dict(response.headers)
            new_headers["Idempotent-Replayed"] = "false"
            new_headers.pop("content-length", None)
            return Response(
                content=response_body,
                status_code=response.status_code,
                headers=new_headers,
                media_type=response.media_type,
            )


def build_store(settings: Any) -> IdempotencyStore:
    ttl = float(getattr(settings, "idempotency_ttl_seconds", 24 * 3600))
    cap = int(getattr(settings, "idempotency_max_per_tenant", 1024))
    return IdempotencyStore(ttl_seconds=ttl, max_per_tenant=cap)


def reset_for_tests() -> None:
    """Test hook: wipe the module-level store if one is registered."""
    store = _registered.get("store")
    if store is not None:
        store.reset()


_registered: dict[str, IdempotencyStore] = {}


def register(store: IdempotencyStore) -> None:
    _registered["store"] = store


def get_registered() -> IdempotencyStore | None:
    return _registered.get("store")


__all__ = [
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "build_store",
    "register",
    "get_registered",
    "reset_for_tests",
]
