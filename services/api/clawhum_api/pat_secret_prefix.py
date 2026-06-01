"""Per-workspace custom PAT secret prefix policy.

Why this exists
---------------
Every clawhum personal access token is minted with the global
``pat_`` prefix so credential scanners can recognise it as a clawhum
token. That is the bare minimum a credential scanner needs.

What enterprise procurement actually asks for, though, is:

   "When one of our developers accidentally commits a clawhum
   token to a public repository, can our internal secret scanner
   (GitHub secret scanning, Trufflehog, custom pre-commit) tell
   immediately that the token belongs to *our* workspace and route
   the leak to *our* incident channel without paging every other
   clawhum customer?"

With the global ``pat_`` prefix the answer is no, scanners only see
"a clawhum token from somewhere". With this module a workspace owner
registers a short, custom prefix (e.g. ``acme``); every PAT minted
or rotated after that point is shaped as
``pat_<workspace_prefix>_<random>``. Existing PATs are left alone
because changing their secret value would break every running
deployment.

The prefix is constrained to ``[a-z0-9-]{2,16}``, lower-case only,
so it is safe to embed in regex catalogues like
``pat_acme_[A-Za-z0-9_-]{20,}`` that secret scanners ingest. The
policy is stored per tenant on the same append-only JSONL pattern
the rest of the policy modules use; cross-tenant lookups are
impossible because the store is keyed by tenant id.

Empty / unset policy means "no custom prefix" and the global
``pat_`` shape is used, so existing tenants keep working unchanged.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from clawhum_core.settings import get_settings


# Lower-case alphanumeric plus dash, 2-16 chars. Chosen so a scanner
# regex can be ``pat_<prefix>_[A-Za-z0-9_-]{20,}`` without needing
# to escape anything. We deliberately forbid underscore so the
# ``_`` between the workspace prefix and the random body remains an
# unambiguous separator.
_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,14}[a-z0-9])?$")
_MAX_LEN = 16
_MIN_LEN = 2

_LOCK = Lock()
_CACHE: dict[str, "Policy"] | None = None
_CACHE_PATH: Path | None = None


class InvalidPrefixError(ValueError):
    """Raised when a submitted prefix fails validation. User-safe."""


def normalise(raw: str | None) -> str:
    """Return a canonical prefix, or ``""`` for "no policy".

    Strips whitespace and lower-cases the input. Empty / None means
    clear the policy. Non-empty values must match ``_PREFIX_RE`` or
    :class:`InvalidPrefixError` is raised with a user-safe message.
    """
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    if not s:
        return ""
    if len(s) < _MIN_LEN or len(s) > _MAX_LEN:
        raise InvalidPrefixError(
            f"prefix must be {_MIN_LEN}-{_MAX_LEN} chars"
        )
    if not _PREFIX_RE.match(s):
        raise InvalidPrefixError(
            "prefix must be lower-case [a-z0-9-], must start and end "
            "with [a-z0-9], no leading/trailing dash, no underscore"
        )
    return s


@dataclass(frozen=True)
class Policy:
    tenant_id: str
    prefix: str  # "" means no custom prefix
    updated_at: float
    updated_by: str

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "prefix": self.prefix,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _path() -> Path:
    return Path(get_settings().pat_secret_prefix_path)


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
                try:
                    pfx = normalise(row.get("prefix") or "")
                except InvalidPrefixError:
                    # A malformed historical row should never poison
                    # the cache; treat it as "no policy" for that
                    # tenant so mint behaviour remains predictable.
                    continue
                out[tid] = Policy(
                    tenant_id=tid,
                    prefix=pfx,
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


def get_policy(tenant_id: str) -> Policy | None:
    with _LOCK:
        return _load_locked().get(tenant_id)


def get_prefix(tenant_id: str) -> str:
    """Return the workspace prefix or ``""`` when no policy is set."""
    p = get_policy(tenant_id)
    return p.prefix if p else ""


def has_policy(tenant_id: str) -> bool:
    return bool(get_prefix(tenant_id))


def set_policy(*, tenant_id: str, prefix: str | None, updated_by: str) -> Policy:
    """Replace the workspace PAT secret prefix.

    Pass ``None`` or ``""`` to clear the policy. Raises
    :class:`InvalidPrefixError` on a malformed prefix.
    """
    safe_prefix = normalise(prefix)
    row = Policy(
        tenant_id=tenant_id,
        prefix=safe_prefix,
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


def scanner_regex(prefix: str) -> str:
    """Build a copy-pasteable scanner regex for a workspace prefix.

    Returned shape: ``pat_<prefix>_[A-Za-z0-9_-]{20,}``. Empty prefix
    returns the global fallback ``pat_[A-Za-z0-9_-]{20,}``.
    """
    if prefix:
        return rf"pat_{re.escape(prefix)}_[A-Za-z0-9_-]{{20,}}"
    return r"pat_[A-Za-z0-9_-]{20,}"
