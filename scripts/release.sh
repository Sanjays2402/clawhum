#!/usr/bin/env bash
set -euo pipefail
v="${1:?usage: release.sh vX.Y.Z}"
git tag -a "$v" -m "release $v"
git push origin "$v"
