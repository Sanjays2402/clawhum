"""Per-workspace data retention policy.

Each tenant can declare how long different categories of data live
before they are purged. Categories map to the JSONL stores already in
use; setting a category to 0 days means "keep forever" so the feature
is strictly opt-in for existing workspaces.

Two enforcement modes ship together:

1. Read-time filtering: callers can wrap a row iterator with
   ``filter_expired`` to hide rows older than the policy without
   touching disk. This is cheap and keeps read paths honest even when
   the operator forgets to schedule a sweep.
2. Hard delete: ``enforce_policy`` rewrites each tracked JSONL file in
   place, dropping rows for the calling tenant that exceed the policy.
   Other tenants' rows are preserved exactly. Returns a per-category
   count of removed rows so the admin UI can show what changed.

Storage follows the same JSONL pattern used by ``ip_allowlist`` and
``sso_store`` so multi-tenant deployments do not need a database to
enable this control. Reads cache parsed policies in process and
invalidate on every write.

The set of categories is intentionally small and stable; adding a new
category here is a deliberate change because every storage path that
should respect retention must call ``filter_expired`` explicitly.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from clawhum_core.settings import get_settings

# Category -> path settings attribute name. Stored separately from the
# settings module so this file can be unit tested without booting the
# full FastAPI app.
CATEGORIES: dict[str, str] = {
    "history": "history_path",
    "feedback": "feedback_path",
    "audit": "audit_log_path",
    "webhook_deliveries": "webhook_deliveries_path",
}

# Each policy field name maps to the category key. Kept as a tuple so
# the API schema and storage layer can iterate in a deterministic order.
POLICY_FIELDS: tuple[str, ...] = tuple(CATEGORIES.keys())

_LOCK = Lock()
_CACHE: dict[str, "RetentionPolicy"] | None = None
_CACHE_PATH: Path | None = None


@dataclass(frozen=True)
class RetentionPolicy:
    tenant_id: str
    history_days: int = 0
    feedback_days: int = 0
    audit_days: int = 0
    webhook_deliveries_days: int = 0
    updated_at: float = 0.0
    updated_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "history_days": self.history_days,
            "feedback_days": self.feedback_days,
            "audit_days": self.audit_days,
            "webhook_deliveries_days": self.webhook_deliveries_days,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    def days_for(self, category: str) -> int:
        return int(getattr(self, f"{category}_days", 0))

    def is_empty(self) -> bool:
        return all(self.days_for(c) == 0 for c in POLICY_FIELDS)


def _policy_path() -> Path:
    # Stored alongside ip_allowlist / sso so operators only have one data dir.
    s = get_settings()
    p = (s.ip_allowlist_path.parent / "retention.jsonl").resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def reset_cache() -> None:
    """Drop the in-process cache. Tests and lifespan startup call this."""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def _load_all() -> dict[str, RetentionPolicy]:
    global _CACHE, _CACHE_PATH
    path = _policy_path()
    with _LOCK:
        if _CACHE is not None and _CACHE_PATH == path:
            return _CACHE
        out: dict[str, RetentionPolicy] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = str(rec.get("tenant_id") or "").strip().lower()
                if not tid:
                    continue
                # Last write per tenant wins; file is rewritten on update
                # so duplicates only occur if an operator hand-edits it.
                out[tid] = RetentionPolicy(
                    tenant_id=tid,
                    history_days=int(rec.get("history_days", 0) or 0),
                    feedback_days=int(rec.get("feedback_days", 0) or 0),
                    audit_days=int(rec.get("audit_days", 0) or 0),
                    webhook_deliveries_days=int(rec.get("webhook_deliveries_days", 0) or 0),
                    updated_at=float(rec.get("updated_at", 0.0) or 0.0),
                    updated_by=str(rec.get("updated_by", "") or ""),
                )
        _CACHE = out
        _CACHE_PATH = path
        return out


def get_policy(tenant_id: str) -> RetentionPolicy:
    """Return the policy for tenant, or an all-zero default."""
    tid = (tenant_id or "").strip().lower()
    if not tid:
        tid = "default"
    return _load_all().get(tid, RetentionPolicy(tenant_id=tid))


def set_policy(
    tenant_id: str,
    *,
    history_days: int = 0,
    feedback_days: int = 0,
    audit_days: int = 0,
    webhook_deliveries_days: int = 0,
    updated_by: str = "",
) -> RetentionPolicy:
    """Upsert and persist the policy for a tenant.

    Days values are clamped to non-negative integers; callers should
    validate upstream so invalid input becomes a 400, not a silent zero.
    """
    tid = (tenant_id or "").strip().lower()
    if not tid:
        raise ValueError("tenant_id required")
    days = {
        "history_days": max(0, int(history_days)),
        "feedback_days": max(0, int(feedback_days)),
        "audit_days": max(0, int(audit_days)),
        "webhook_deliveries_days": max(0, int(webhook_deliveries_days)),
    }
    pol = RetentionPolicy(
        tenant_id=tid,
        **days,
        updated_at=time.time(),
        updated_by=updated_by or "",
    )
    path = _policy_path()
    global _CACHE, _CACHE_PATH
    with _LOCK:
        existing = _load_all().copy() if _CACHE is not None else {}
        existing[tid] = pol
        # Atomic rewrite. JSONL with one record per tenant; small files.
        tmp = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(path.parent), prefix=".retention.", suffix=".tmp"
        )
        try:
            for rec in existing.values():
                tmp.write(json.dumps(rec.to_dict(), separators=(",", ":")) + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            try:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
            except OSError:
                pass
        # Invalidate cache; next read reloads from disk so we never
        # serve a stale view even if another worker wrote concurrently.
        _CACHE = None
        _CACHE_PATH = None
    return pol


def _row_timestamp(row: dict[str, Any]) -> float:
    """Pick the most likely timestamp field on a JSONL row.

    Different stores use different field names. We try the common ones
    in priority order; rows with no timestamp are treated as "fresh"
    (never expire) so we never silently drop unlabelled data.
    """
    for key in ("created_at", "ts", "timestamp", "updated_at", "received_at"):
        v = row.get(key)
        if v is None:
            continue
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return 0.0


def filter_expired(
    rows: Iterable[dict[str, Any]],
    category: str,
    tenant_id: str,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Drop rows older than the policy for this tenant+category.

    Rows from other tenants pass through unchanged so callers can use
    this on the raw scan before tenant scoping if they want, or after.
    Rows without a tenant_id are treated as belonging to the default
    tenant for retention purposes, matching ``tenant.scope_rows``.
    """
    if category not in CATEGORIES:
        raise ValueError(f"unknown retention category: {category}")
    pol = get_policy(tenant_id)
    days = pol.days_for(category)
    if days <= 0:
        return list(rows)
    cutoff = (now if now is not None else time.time()) - (days * 86400.0)
    out: list[dict[str, Any]] = []
    for row in rows:
        row_tenant = row.get("tenant_id") or "default"
        if row_tenant != tenant_id:
            out.append(row)
            continue
        ts = _row_timestamp(row)
        if ts == 0.0 or ts >= cutoff:
            out.append(row)
    return out


def enforce_policy(tenant_id: str, *, now: float | None = None) -> dict[str, int]:
    """Hard-delete rows that exceed the policy. Returns per-category counts.

    Only rows belonging to ``tenant_id`` are eligible for deletion.
    Files are rewritten atomically; if a category's file is absent or
    the policy is 0 days, that category is skipped.
    """
    pol = get_policy(tenant_id)
    removed: dict[str, int] = {c: 0 for c in POLICY_FIELDS}
    if pol.is_empty():
        return removed
    # Defense in depth: never purge a tenant under legal hold, even if a
    # caller bypassed the route layer (cron, CLI, internal worker).
    from . import legal_hold as _lh
    if _lh.is_on_hold(tenant_id):
        raise _lh.LegalHoldActive(_lh.active_hold(tenant_id))
    s = get_settings()
    cutoff_now = now if now is not None else time.time()
    for category in POLICY_FIELDS:
        days = pol.days_for(category)
        if days <= 0:
            continue
        path = Path(getattr(s, CATEGORIES[category]))
        if not path.exists():
            continue
        cutoff = cutoff_now - (days * 86400.0)
        kept: list[str] = []
        n_removed = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                # Preserve unparseable lines; never destroy data we cannot read.
                kept.append(raw)
                continue
            row_tenant = row.get("tenant_id") or "default"
            if row_tenant != tenant_id:
                kept.append(raw)
                continue
            ts = _row_timestamp(row)
            if ts == 0.0 or ts >= cutoff:
                kept.append(raw)
                continue
            n_removed += 1
        if n_removed == 0:
            continue
        tmp = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            for line in kept:
                tmp.write(line + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            try:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
            except OSError:
                pass
        removed[category] = n_removed
    return removed
