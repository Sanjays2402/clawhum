from __future__ import annotations
import json
from pathlib import Path
from clawhum_core.types import Track


def write_metadata(path: str | Path, tracks: list[Track]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        for t in tracks:
            f.write(json.dumps(t.to_dict()) + "\n")


def read_metadata(path: str | Path) -> list[Track]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[Track] = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Track.from_dict(json.loads(line)))
    return out


def upsert_metadata(path: str | Path, tracks: list[Track]) -> list[Track]:
    cur = {t.id: t for t in read_metadata(path)}
    for t in tracks:
        cur[t.id] = t
    merged = list(cur.values())
    write_metadata(path, merged)
    return merged
