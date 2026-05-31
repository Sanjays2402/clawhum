"""Tamper-evidence verifier for the workspace audit log.

Every line written by ``audit.write_event`` includes two integrity
fields, ``prev_hash`` and ``entry_hash``. The hash of each entry is
sha256 of ``prev_hash || canonical_json(entry_without_entry_hash)``.
The first entry in a fresh file uses the well-known genesis hash so a
verifier can distinguish \"file starts at the beginning of history\"
from \"someone truncated the head of the file\".

This module re-derives those digests for every line and reports the
result so a procurement reviewer can prove no entry was edited,
deleted, or reordered after it was written. It is read-only and does
not mutate the file.

The verifier is intentionally tolerant of size-based rotation: it
walks the active file plus every sibling backup, in chronological
order, and treats each file as an independent chain that starts at
genesis. Rotation creates a new file, so a fresh chain segment at the
top of every backup is expected behaviour and not a tamper signal.

For multi-replica deployments the audit file lives on shared storage
or each replica owns a partition by tenant; either way the local file
is the source of truth for what this replica wrote.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .audit import AUDIT_GENESIS_HASH, hash_entry


@dataclass(frozen=True)
class FileResult:
    path: str
    entries: int
    valid: int
    first_bad_line: int | None
    reason: str | None
    head_prev_hash: str | None
    tail_entry_hash: str | None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "entries": self.entries,
            "valid": self.valid,
            "ok": self.first_bad_line is None,
            "first_bad_line": self.first_bad_line,
            "reason": self.reason,
            "head_prev_hash": self.head_prev_hash,
            "tail_entry_hash": self.tail_entry_hash,
        }


@dataclass
class VerifyResult:
    ok: bool
    files: list[FileResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "files": [f.to_dict() for f in self.files],
        }


def _verify_one(path: Path) -> FileResult:
    if not path.exists():
        return FileResult(
            path=str(path),
            entries=0,
            valid=0,
            first_bad_line=None,
            reason=None,
            head_prev_hash=None,
            tail_entry_hash=None,
        )
    entries = 0
    valid = 0
    head_prev: str | None = None
    last_digest = AUDIT_GENESIS_HASH
    first_bad: int | None = None
    reason: str | None = None
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            entries += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                if first_bad is None:
                    first_bad = lineno
                    reason = f"json decode failed: {exc}"
                continue
            stored_prev = row.get("prev_hash")
            stored_digest = row.get("entry_hash")
            if not isinstance(stored_prev, str) or not isinstance(stored_digest, str):
                if first_bad is None:
                    first_bad = lineno
                    reason = "missing prev_hash or entry_hash field"
                continue
            if entries == 1:
                head_prev = stored_prev
            if stored_prev != last_digest:
                if first_bad is None:
                    first_bad = lineno
                    reason = (
                        f"prev_hash mismatch: expected {last_digest[:12]}..., "
                        f"got {stored_prev[:12]}..."
                    )
                # Keep walking so we know how many entries are downstream
                # of the break; for the chain test the answer is "all".
                last_digest = stored_digest
                continue
            recomputed = hash_entry(stored_prev, {k: v for k, v in row.items() if k != "entry_hash"})
            if recomputed != stored_digest:
                if first_bad is None:
                    first_bad = lineno
                    reason = "entry_hash mismatch (body edited after write)"
                last_digest = stored_digest
                continue
            valid += 1
            last_digest = stored_digest
    tail = last_digest if entries else None
    return FileResult(
        path=str(path),
        entries=entries,
        valid=valid,
        first_bad_line=first_bad,
        reason=reason,
        head_prev_hash=head_prev,
        tail_entry_hash=tail,
    )


def _rotated_siblings(active: Path) -> list[Path]:
    out: list[Path] = []
    n = 1
    while True:
        candidate = active.with_name(f"{active.name}.{n}")
        if not candidate.exists():
            break
        out.append(candidate)
        n += 1
    return out


def verify_chain(active_path: Path, include_rotated: bool = True) -> VerifyResult:
    """Walk the active audit file and every rotated sibling, return the result.

    Each file is checked as an independent chain that starts at the
    genesis hash because rotation produces a fresh empty file. The
    overall result is ok only when every file's chain verifies cleanly.
    """
    files: list[FileResult] = []
    targets: Iterable[Path]
    if include_rotated:
        targets = [active_path] + _rotated_siblings(active_path)
    else:
        targets = [active_path]
    overall = True
    for p in targets:
        res = _verify_one(p)
        if res.first_bad_line is not None:
            overall = False
        files.append(res)
    return VerifyResult(ok=overall, files=files)
