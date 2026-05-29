#!/usr/bin/env bash
set -euo pipefail
source .venv/bin/activate
export PYTHONPATH=packages/core:packages/audio:packages/embed:packages/index:packages/match:packages/library:services/api:services/indexer:cli
exec uvicorn clawhum_api.app:app --host 0.0.0.0 --port 7451 --reload
