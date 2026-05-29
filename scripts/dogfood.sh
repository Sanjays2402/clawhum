#!/usr/bin/env bash
set -euo pipefail
[[ -d .venv ]] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -e ".[dev]"
python scripts/seed_fixtures.py ./data/audio
export PYTHONPATH=packages/core:packages/audio:packages/embed:packages/index:packages/match:packages/library:services/api:services/indexer:cli:.
clawhum index ./data/audio --no-clap
clawhum stats
clawhum serve
