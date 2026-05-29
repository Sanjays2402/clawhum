#!/usr/bin/env bash
set -euo pipefail
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
echo "ok"
