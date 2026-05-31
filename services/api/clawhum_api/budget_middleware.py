"""Per-workspace monthly spend cap enforcement.

Sits just inside the rate limiter so a request rejected by the budget
does not consume the per-key bucket. Skips reads, health, metrics, and
auth failures so only chargeable work counts against the cap.

When a workspace has an active hard-stop budget and the rolling 30 day
chargeable count is at or above the cap, requests are rejected with
HTTP 402 Payment Required and a machine-readable JSON body so client
SDKs can surface the right upsell. When ``hard_stop`` is False the
request still flows through (audit-only rollout mode) but every
response carries the same ``X-Budget-*`` headers a caller would use
to back off.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import budget_store, pat_store
from .api_keys import ANON_TENANT_ID, get_registry
from .usage import classify, month_count

# Routes that must never be billed or blocked. Mirrors the rate limit
# skiplist plus the budget-admin surface itself so an admin who has
# already hit the cap can still raise it.
_SKIP_PREFIXES = (
    "/health",
    "/ready",
    "/metrics",
    "/budget",
    "/v1/budget",
)


def _skip(path: str) -> bool:
    if path in {"/health", "/ready", "/metrics"}:
        return True
    for p in _SKIP_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _resolve_tenant(request) -> str:
    """Resolve the workspace tenant from the API key header without
    waiting for the route's auth dependency to populate request.state.

    Mirrors the lookup the rate limiter does so budget enforcement
    happens before the route runs and never charges the workspace for
    work it cannot bill.
    """
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        entry = get_registry().lookup(api_key)
        if entry is not None and entry.tenant_id:
            return entry.tenant_id
        if pat_store.looks_like_pat(api_key):
            pat = pat_store.lookup_by_secret(api_key)
            if pat is not None and pat.tenant_id:
                return pat.tenant_id
    return ANON_TENANT_ID


class BudgetMiddleware(BaseHTTPMiddleware):
    """Enforce the workspace monthly cap on chargeable requests."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        method = request.method
        chargeable = classify(path, method) is not None and not _skip(path)

        tenant = _resolve_tenant(request)
        budget = budget_store.get_budget(tenant)

        # Hard stop *before* the route runs so we never do the work we
        # cannot bill. The count comparison uses the current rolling
        # window so callers naturally recover once old events age out.
        if chargeable and budget.monthly_cap > 0:
            used = month_count(tenant)
            if budget.hard_stop and used >= budget.monthly_cap:
                return JSONResponse(
                    {
                        "detail": "workspace monthly budget exhausted",
                        "code": "budget_exhausted",
                        "monthly_cap": budget.monthly_cap,
                        "used": used,
                        "window_sec": 86_400 * 30,
                    },
                    status_code=402,
                    headers={
                        "X-Budget-Limit": str(budget.monthly_cap),
                        "X-Budget-Used": str(used),
                        "X-Budget-Remaining": "0",
                        "X-Budget-Status": "exhausted",
                    },
                )

        response = await call_next(request)

        # Advertise the budget on every response so well-behaved clients
        # can back off before they hit 402. Cheap: month_count is O(1)
        # amortized once the tenant is loaded.
        if budget.monthly_cap > 0:
            used_after = month_count(tenant)
            remaining = max(0, budget.monthly_cap - used_after)
            pct = int((used_after / budget.monthly_cap) * 100) if budget.monthly_cap else 0
            if remaining == 0:
                status_label = "exhausted"
            elif budget.soft_threshold_pct > 0 and pct >= budget.soft_threshold_pct:
                status_label = "warning"
            else:
                status_label = "ok"
            response.headers["X-Budget-Limit"] = str(budget.monthly_cap)
            response.headers["X-Budget-Used"] = str(used_after)
            response.headers["X-Budget-Remaining"] = str(remaining)
            response.headers["X-Budget-Status"] = status_label
            if not budget.hard_stop:
                response.headers["X-Budget-Enforcement"] = "audit"
        return response
