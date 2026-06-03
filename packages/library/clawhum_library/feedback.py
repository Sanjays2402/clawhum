from __future__ import annotations
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Iterable


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
    track_ids: "Iterable[str] | None" = None,
    exclude_track_ids: "Iterable[str] | None" = None,
    exclude_query_ids: "Iterable[str] | None" = None,
    vote: int | None = None,
    since: float | None = None,
    until: float | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
) -> int:
    """Delete feedback rows matching the given filters.

    At least one of query_id/track_id/track_ids/vote/since/until/min_score/
    max_score must be provided; a no-filter call raises ValueError so a misuse
    never wipes the whole log. `since` keeps rows whose ts is strictly less
    than the bound; `until` keeps rows whose ts is greater than or equal to
    the bound. Rows without a numeric ts are always kept when a time bound is
    in play so undated entries are never silently purged. Similarly, rows
    without a numeric score are always kept when a score bound is in play so
    entries with a missing/non-numeric score are never silently purged.
    `track_ids` is an allowlist (set of ids to delete) and ANDs with
    `track_id`; an empty allowlist matches no rows so a title/artist resolver
    that returned no hits never wipes the log. `exclude_track_ids` /
    `exclude_query_ids` are denylists: any row whose track_id or query_id is
    in the corresponding set is always kept, so an operator can scope a bulk
    purge to skip known-good tracks or a curated set of sessions (e.g.
    `--vote -1 --until 30d --exclude-track t-keepme` to age out old down-votes
    while preserving a track you still care about). Empty denylists are a
    no-op. Returns the number of rows removed. The rewrite is atomic via a tempfile + os.replace in the same
    directory, so a crash mid-write cannot leave a truncated log.
    """
    track_id_set: set[str] | None = None
    if track_ids is not None:
        track_id_set = {str(t) for t in track_ids}
    exclude_track_set: set[str] = {str(t) for t in (exclude_track_ids or [])}
    exclude_query_set: set[str] = {str(q) for q in (exclude_query_ids or [])}
    if (
        query_id is None
        and track_id is None
        and track_id_set is None
        and vote is None
        and since is None
        and until is None
        and min_score is None
        and max_score is None
    ):
        raise ValueError("delete_feedback requires at least one filter")
    if track_id_set is not None and not track_id_set:
        # Empty allowlist matches no rows; nothing to do. Returning early also
        # protects against the upstream resolver (e.g. title/artist with zero
        # matches) accidentally being treated as "no filter".
        return 0
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
            if exclude_query_set and str(row.get("query_id", "")) in exclude_query_set:
                kept.append(row)
                continue
            if exclude_track_set and str(row.get("track_id", "")) in exclude_track_set:
                kept.append(row)
                continue
            if query_id is not None and row.get("query_id") != query_id:
                kept.append(row)
                continue
            if track_id is not None and row.get("track_id") != track_id:
                kept.append(row)
                continue
            if track_id_set is not None and str(row.get("track_id", "")) not in track_id_set:
                kept.append(row)
                continue
            if vote is not None and row.get("vote") != vote:
                kept.append(row)
                continue
            ts = row.get("ts")
            if since is not None or until is not None:
                if not isinstance(ts, (int, float)):
                    kept.append(row)
                    continue
                if since is not None and ts < since:
                    kept.append(row)
                    continue
                if until is not None and ts >= until:
                    kept.append(row)
                    continue
            if min_score is not None or max_score is not None:
                score = row.get("score")
                if not isinstance(score, (int, float)) or isinstance(score, bool):
                    kept.append(row)
                    continue
                if min_score is not None and score < min_score:
                    kept.append(row)
                    continue
                if max_score is not None and score > max_score:
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
