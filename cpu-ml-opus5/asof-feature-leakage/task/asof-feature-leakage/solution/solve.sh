#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${WORKSPACE_DIR:-/workspace/target}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOLD_DIR="${GOLD_DIR:-$SCRIPT_DIR/gold}"
PY="${PYTHON:-python3}"

cd "$WORKSPACE"
rm -rf .envstate

cp "$GOLD_DIR/featurize.py" src/featurize.py
cp "$GOLD_DIR/split.py"     src/split.py

"$PY" env_cli.py inspect >/dev/null
"$PY" env_cli.py profile >/dev/null
for cursor in 0 1 2 3 4 5 6 7 8 9 10 11 12; do
  "$PY" env_cli.py sample_records --cursor "$cursor" >/dev/null
done

"$PY" env_cli.py reproduce
"$PY" env_cli.py deploy
"$PY" env_cli.py validate
"$PY" env_cli.py diagnostics.read
"$PY" env_cli.py recovery.apply --family stale_cache
"$PY" env_cli.py validate
"$PY" env_cli.py promote
"$PY" env_cli.py submit

echo "oracle trajectory complete"
