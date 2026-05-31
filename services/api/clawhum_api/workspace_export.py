"""Workspace-wide GDPR/SOC2 data export.

The per-actor export at ``/v1/privacy/export`` only returns audit
events attributed to the caller. Enterprise buyers also need a
workspace-wide bundle covering every store the tenant has data in:
history, feedback, audit, webhooks (endpoints + deliveries),
collections, shares, usage, history views, members, retention,
SSO config (secrets redacted), IP allowlist, and quota plan.

This module produces that bundle. It reads each JSONL store, filters
by ``tenant_id`` using the same scoping rule the rest of the app
uses (rows without a tenant_id are treated as legacy and surfaced
only to the ``default`` tenant), redacts secrets, and packages the
result as a single in-memory ZIP. Every category lands in its own
``<category>.jsonl`` file plus a top-level ``manifest.json`` so a
downstream tool can verify counts without re-parsing.

Reads are streaming and best-effort: if a store file is missing the
category lands with zero rows rather than failing the whole export.
Cross-tenant rows can never leak because every category goes through
``scope_rows`` (or an equivalent per-store filter) before serialising.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from clawhum_core.settings import get_settings
from clawhum_core.version import __version__

from .tenant import scope_rows

# Categories that are pure tenant-scoped JSONL stores; the helper just
# reads the file, filters by tenant_id, and dumps the rows. Order is
# deterministic so the ZIP layout is stable for diffing.
_JSONL_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("history", "history_path"),
    ("history_views", "history_views_path"),
    ("feedback", "feedback_path"),
    ("collections", "collections_path"),
    ("shares", "shares_path"),
    ("usage", "usage_path"),
    ("webhooks", "webhooks_path"),
    ("webhook_deliveries", "webhook_deliveries_path"),
    ("webhook_allowlist", "webhook_allowlist_path"),
    ("members", "members_path"),
    ("ip_allowlist", "ip_allowlist_path"),
    ("quotas", "quota_path"),
    ("sso", "sso_path"),
    ("personal_access_tokens", "pat_path"),
)


# Fields that must never leave the building, even inside an admin
# initiated export. We redact rather than drop so row shape and counts
# stay intact for downstream tooling.
_SECRET_FIELDS: frozenset[str] = frozenset({
    "client_secret",
    "secret",
    "token",
    "token_hash",
    "endpoint_secret",
    "signing_secret",
    "totp_secret",
    "password",
})

_REDACTED = "redacted"


@dataclass(frozen=True)
class ExportManifest:
    tenant_id: str
    generated_at: float
    app_version: str
    row_counts: dict[str, int]
    total_rows: int
    sha256: str  # hash of the concatenated category payloads, for tamper-evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "generated_at": self.generated_at,
            "generated_at_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.generated_at)
            ),
            "app_version": self.app_version,
            "row_counts": dict(sorted(self.row_counts.items())),
            "total_rows": self.total_rows,
            "sha256": self.sha256,
            "schema_version": 1,
            "notes": [
                "Each <category>.jsonl file is newline-delimited JSON,"
                " one record per line, scoped to tenant_id.",
                "Secret fields (client_secret, token, endpoint_secret,"
                " totp_secret, password) are replaced with the literal"
                " string 'redacted'. Row shape and counts are preserved.",
                "Rows written before multi tenancy was enabled have no"
                " tenant_id tag and are surfaced only to the 'default'"
                " tenant, matching scope_rows() elsewhere in the API.",
                "Audit log is included as audit.jsonl. The append-only"
                " forensic log is preserved on disk; this bundle is a"
                " filtered copy, not a deletion.",
            ],
        }


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Skip corrupt lines rather than abort the export.
                    continue
    except OSError:
        return


def _redact(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in _SECRET_FIELDS and v not in (None, "", 0):
            out[k] = _REDACTED
        else:
            out[k] = v
    return out


def _category_rows(
    setting_attr: str, tenant_id: str
) -> list[dict[str, Any]]:
    settings = get_settings()
    path = getattr(settings, setting_attr, None)
    if path is None:
        return []
    rows = list(_iter_jsonl(Path(path)))
    scoped = scope_rows(rows, tenant_id)
    return [_redact(r) for r in scoped]


def _retention_rows(tenant_id: str) -> list[dict[str, Any]]:
    """Snapshot of the active retention policy for the tenant."""
    try:
        from . import retention as _retention

        policy = _retention.get_policy(tenant_id)
    except Exception:
        return []
    if policy is None or getattr(policy, "is_empty", lambda: True)():
        return []
    if hasattr(policy, "to_dict"):
        return [_redact(policy.to_dict())]
    return []


def _audit_rows(tenant_id: str) -> list[dict[str, Any]]:
    """Pull audit events for this tenant from the active + rotated logs."""
    settings = get_settings()
    path = Path(settings.audit_log_path)
    siblings: list[Path] = [path] if path.exists() else []
    n = 1
    while True:
        candidate = path.with_name(f"{path.name}.{n}")
        if not candidate.exists():
            break
        siblings.append(candidate)
        n += 1
    out: list[dict[str, Any]] = []
    for p in siblings:
        for row in _iter_jsonl(p):
            if row.get("tenant_id") == tenant_id:
                out.append(_redact(row))
    return out


def build_export(tenant_id: str) -> tuple[bytes, ExportManifest]:
    """Build the workspace export ZIP for ``tenant_id``.

    Returns the ZIP bytes and the manifest. The manifest is also
    written into the ZIP as ``manifest.json`` so consumers can verify
    counts without round-tripping to the server.
    """
    counts: dict[str, int] = {}
    payloads: dict[str, bytes] = {}

    for category, setting_attr in _JSONL_CATEGORIES:
        rows = _category_rows(setting_attr, tenant_id)
        payloads[category] = _serialise_jsonl(rows)
        counts[category] = len(rows)

    audit = _audit_rows(tenant_id)
    payloads["audit"] = _serialise_jsonl(audit)
    counts["audit"] = len(audit)

    retention_rows = _retention_rows(tenant_id)
    payloads["retention_policy"] = _serialise_jsonl(retention_rows)
    counts["retention_policy"] = len(retention_rows)

    hasher = hashlib.sha256()
    for cat in sorted(payloads):
        hasher.update(cat.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payloads[cat])

    manifest = ExportManifest(
        tenant_id=tenant_id,
        generated_at=time.time(),
        app_version=__version__,
        row_counts=counts,
        total_rows=sum(counts.values()),
        sha256=hasher.hexdigest(),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True),
        )
        for category, blob in sorted(payloads.items()):
            zf.writestr(f"{category}.jsonl", blob)
        zf.writestr(
            "README.txt",
            _readme_text(manifest),
        )
    return buf.getvalue(), manifest


def _serialise_jsonl(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    lines = [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _readme_text(manifest: ExportManifest) -> str:
    return (
        "ClawHum workspace data export\n"
        "=============================\n"
        f"tenant_id     : {manifest.tenant_id}\n"
        f"generated_at  : {manifest.generated_at:.0f}\n"
        f"app_version   : {manifest.app_version}\n"
        f"total_rows    : {manifest.total_rows}\n"
        f"sha256        : {manifest.sha256}\n"
        "\n"
        "Files:\n"
        "  manifest.json       Counts, integrity hash, schema info.\n"
        "  <category>.jsonl    Newline-delimited JSON, tenant-scoped.\n"
        "\n"
        "Secrets (client_secret, endpoint_secret, totp_secret, token,\n"
        "password) are replaced with the literal string 'redacted'.\n"
        "Audit events are included as audit.jsonl. The on-disk audit\n"
        "log is append-only and is preserved; this bundle is a filtered\n"
        "copy, not a deletion.\n"
    )


def export_filename(tenant_id: str, now: float | None = None) -> str:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now or time.time()))
    safe = "".join(c for c in tenant_id if c.isalnum() or c in "-_") or "workspace"
    return f"clawhum-workspace-{safe}-{ts}.zip"
