"""Per-tenant usage tracking for billing-ready quota meters.

Records one event per chargeable API call to a JSONL file at
settings.usage_path (default ./data/usage.jsonl), then aggregates
counts on read for the calling tenant over rolling minute, day, and
month windows. Designed to power an in-app usage meter and an upgrade
CTA without bolting on a database.

Only "chargeable" routes are counted (match, batch, pitch, share
create, history write, webhook delivery trigger). GET reads, health,
metrics, and 4xx/5xx responses from the route itself are skipped so
the meter reflects real billable work.

The module exposes:
- UsageRecorderMiddleware: starlette middleware appending events.
- recent_counts(tenant_id): aggregated counts for the current minute,
  day, and month for that tenant.
- monthly_quota(): the configured free-tier monthly quota.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from clawhum_core.settings import get_settings
from starlette.middleware.base import BaseHTTPMiddleware

from .api_keys import ANON_TENANT_ID

# Path-prefix -> chargeable event class. Order matters: longest match wins.
_CHARGEABLE: tuple[tuple[str, str], ...] = (
    ("/match", "match"),
    ("/batch", "batch"),
    ("/pitch", "pitch"),
    ("/share", "share"),
    ("/history", "history"),
    ("/webhooks", "webhook"),
)

_WRITE_LOCK = threading.Lock()

_DEFAULT_FREE_QUOTA = int(os.environ.get("CLAWHUM_FREE_QUOTA_MONTH", "1000"))


def _store_path() -> Path:
    p = getattr(get_settings(), "usage_path", None)
    if p is None:
        p = Path("./data/usage.jsonl")
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def monthly_quota() -> int:
    """Return the configured monthly free-tier quota in requests."""
    return _DEFAULT_FREE_QUOTA


def _classify(path: str, method: str) -> str | None:
    """Return the event class for a request, or None if not chargeable."""
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return None
    # Allow probing requests like the webhook list to escape billing.
    for prefix, name in _CHARGEABLE:
        if path == prefix or path.startswith(prefix + "/"):
            return name
    return None


def record_event(tenant_id: str, event: str, ts: float | None = None) -> None:
    """Append a single usage event. Safe to call from request paths."""
    if not tenant_id:
        tenant_id = ANON_TENANT_ID
    row = {
        "ts": float(ts if ts is not None else time.time()),
        "tenant_id": tenant_id,
        "event": event,
    }
    p = _store_path()
    line = json.dumps(row, separators=(",", ":")) + "\n"
    with _WRITE_LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line)


def _iter_events(path: Path):
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


def recent_counts(tenant_id: str, now: float | None = None) -> dict[str, Any]:
    """Aggregate counts for the tenant across minute / day / month windows.

    Returns a dict with totals, a breakdown per event class, and the
    last 30 daily buckets for a sparkline.
    """
    if now is None:
        now = time.time()
    minute_cut = now - 60
    day_cut = now - 86_400
    month_cut = now - 86_400 * 30

    per_event_day: dict[str, int] = defaultdict(int)
    per_event_month: dict[str, int] = defaultdict(int)
    minute_total = 0
    day_total = 0
    month_total = 0
    # 30 daily buckets, oldest first.
    buckets = [0] * 30

    path = _store_path()
    for row in _iter_events(path):
        if row.get("tenant_id") != tenant_id:
            continue
        ts = float(row.get("ts", 0))
        if ts < month_cut:
            continue
        event = str(row.get("event", "other"))
        month_total += 1
        per_event_month[event] += 1
        # Bucket index: 0 is oldest day in the window, 29 is today.
        days_ago = int((now - ts) // 86_400)
        idx = 29 - days_ago
        if 0 <= idx < 30:
            buckets[idx] += 1
        if ts >= day_cut:
            day_total += 1
            per_event_day[event] += 1
        if ts >= minute_cut:
            minute_total += 1

    quota = monthly_quota()
    return {
        "tenant_id": tenant_id,
        "now": now,
        "quota_per_month": quota,
        "minute": {"total": minute_total, "window_sec": 60},
        "day": {
            "total": day_total,
            "window_sec": 86_400,
            "by_event": dict(per_event_day),
        },
        "month": {
            "total": month_total,
            "window_sec": 86_400 * 30,
            "by_event": dict(per_event_month),
            "percent_used": (month_total / quota * 100.0) if quota > 0 else 0.0,
            "remaining": max(0, quota - month_total),
        },
        "daily_buckets": buckets,
    }


class UsageRecorderMiddleware(BaseHTTPMiddleware):
    """Append one usage event per chargeable 2xx request.

    Runs after the route resolves so request.state.tenant_id is set by
    the auth dependency. Failures inside the writer never break the
    request because billing telemetry is best-effort.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            if 200 <= response.status_code < 300:
                event = _classify(request.url.path, request.method)
                if event is not None:
                    tenant = getattr(request.state, "tenant_id", None) or ANON_TENANT_ID
                    record_event(tenant, event)
        except Exception:  # pragma: no cover - telemetry must not break requests
            pass
        return response
