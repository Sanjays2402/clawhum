from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Iterator
from clawhum_core.types import Track

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus"}


def _id_for(path: Path) -> str:
    return "local:" + hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]


def scan_directory(root: str | Path) -> Iterator[Path]:
    root = Path(root)
    if not root.exists():
        return iter(())
    return (p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXT and p.is_file())


class LocalLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def tracks(self) -> list[Track]:
        out: list[Track] = []
        for p in scan_directory(self.root):
            title = p.stem
            artist = ""
            try:
                parts = p.relative_to(self.root).parts
                if len(parts) >= 2:
                    artist = parts[0]
            except Exception:
                pass
            out.append(Track(
                id=_id_for(p), title=title, artist=artist, path=str(p), source="local",
            ))
        return out
