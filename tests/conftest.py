from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in [
    "packages/core", "packages/audio", "packages/embed", "packages/index",
    "packages/match", "packages/library", "services/api", "services/indexer", "cli",
]:
    sys.path.insert(0, str(ROOT / sub))

os.environ.setdefault("CLAWHUM_LOG_JSON", "false")
