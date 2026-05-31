"""Sandbox / dry-run mode for destructive endpoints.

Any DELETE endpoint that supports ``?dry_run=true`` performs all
validation, tenant scoping, and permission checks just like a normal
request, but instead of mutating state it returns a structured preview
of what *would* be deleted. This lets enterprise buyers script destructive
workflows in CI without touching production data.

Contract for every preview response:

```
{
    "dry_run": true,
    "would_delete": {"kind": "<resource>", "id": "<id>", ...},
    "tenant_id": "<tenant>",
    "warnings": [str, ...]   # optional, omitted when empty
}
```

Use ``is_dry_run(request)`` inside a handler. When true, build a
preview with ``preview(kind, ident, **extra)`` and return it directly.
The middleware also makes audit log entries note ``dry_run=true`` so
auditors can tell preview calls apart from real mutations.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def is_dry_run(request: Request) -> bool:
    """Return True when the caller asked for a sandbox preview.

    Accepts ``?dry_run=true`` (any common truthy spelling) or the
    explicit header ``X-Dry-Run: 1``. Both forms are honored so CLI
    tooling that cannot easily edit query strings still works.
    """
    raw = request.query_params.get("dry_run")
    if raw is not None and raw.strip().lower() in _TRUTHY:
        _mark(request)
        return True
    header = request.headers.get("x-dry-run", "")
    if header.strip().lower() in _TRUTHY:
        _mark(request)
        return True
    return False


def _mark(request: Request) -> None:
    """Tag request state so audit + metrics layers can see this was a preview."""
    try:
        request.state.dry_run = True
    except Exception:
        pass


def preview(kind: str, ident: str | None, *, tenant_id: str | None = None,
            warnings: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """Build the standard preview payload returned to dry-run callers."""
    would: dict[str, Any] = {"kind": kind}
    if ident is not None:
        would["id"] = ident
    would.update(extra)
    out: dict[str, Any] = {
        "dry_run": True,
        "would_delete": would,
    }
    if tenant_id is not None:
        out["tenant_id"] = tenant_id
    if warnings:
        out["warnings"] = list(warnings)
    return out
