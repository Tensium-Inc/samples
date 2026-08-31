#!/usr/bin/env bash
set -euo pipefail
workspace="${WORKSPACE_DIR:-/workspace/target}"
task="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gold="${GOLD_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gold}"
python="${PYTHON:-python3}"
cd "$workspace"
cp "$gold/enrich.py" src/enrich.py
cp "$gold/time_contract.py" src/time_contract.py
"$python" env_cli.py inspect >/dev/null
"$python" env_cli.py profile >/dev/null
for cursor in 0 1 2 3 4 5; do "$python" env_cli.py sample_records --cursor "$cursor" >/dev/null; done
"$python" env_cli.py reproduce >/dev/null
"$python" env_cli.py deploy >/dev/null
"$python" env_cli.py validate >/dev/null || true
"$python" env_cli.py diagnostics.read >/dev/null
"$python" env_cli.py recovery.apply --family schema_registry >/dev/null
"$python" env_cli.py validate >/dev/null
"$python" env_cli.py promote >/dev/null
"$python" env_cli.py submit >/dev/null
