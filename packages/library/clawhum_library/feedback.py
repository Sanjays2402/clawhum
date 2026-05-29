from __future__ import annotations
import json
import time
from pathlib import Path


def record_feedback(path: str | Path, query_id: str, track_id: str, score: float, vote: int) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({
            "ts": time.time(), "query_id": query_id, "track_id": track_id,
            "score": score, "vote": vote,
        }) + "\n")


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
