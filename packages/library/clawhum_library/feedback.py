from __future__ import annotations
import json
import os
import tempfile
import time
from pathlib import Path


def record_feedback(
    path: str | Path,
    query_id: str,
    track_id: str,
    score: float,
    vote: int,
    tenant_id: str | None = None,
) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.time(),
        "query_id": query_id,
        "track_id": track_id,
        "score": score,
        "vote": vote,
    }
    if tenant_id:
        row["tenant_id"] = tenant_id
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")


def read_feedback(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def delete_feedback(
    path: str | Path,
    *,
    query_id: str | None = None,
    track_id: str | None = None,
    vote: int | None = None,
) -> int:
    """Delete feedback rows matching the given filters.

    At least one of query_id/track_id/vote must be provided; a no-filter call
    raises ValueError so a misuse never wipes the whole log. Returns the number
    of rows removed. The rewrite is atomic via a tempfile + os.replace in the
    same directory, so a crash mid-write cannot leave a truncated log.
    """
    if query_id is None and track_id is None and vote is None:
        raise ValueError("delete_feedback requires at least one filter")
    p = Path(path)
    if not p.exists():
        return 0
    kept: list[dict] = []
    removed = 0
    with open(p) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if query_id is not None and row.get("query_id") != query_id:
                kept.append(row)
                continue
            if track_id is not None and row.get("track_id") != track_id:
                kept.append(row)
                continue
            if vote is not None and row.get("vote") != vote:
                kept.append(row)
                continue
            removed += 1
    if removed == 0:
        return 0
    fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as out:
            for row in kept:
                out.write(json.dumps(row) + "\n")
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return removed
