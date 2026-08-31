#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${WORKSPACE_DIR:-/workspace/target}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOLD_DIR="${GOLD_DIR:-$SCRIPT_DIR/gold}"
PY="${PYTHON:-python3}"
cd "$WORKSPACE"
for file in __init__.py artifact_loader.py text_views.py ranker.py cli.py; do cp "$GOLD_DIR/$file" "src/$file"; done
for op in inspect.sources inspect.schema audit.query profile.corpus sample.records test.visible reproduce deploy validate.runtime diagnostics.read; do "$PY" env_cli.py "$op"; done
"$PY" env_cli.py recovery.apply --family stale_vector_cache
for op in validate.runtime audit.provenance audit.lexical audit.fusion deploy.ranker validate.ranker audit.balance; do "$PY" env_cli.py "$op"; done
"$PY" env_cli.py shadow.challenge --probe allocator
"$PY" env_cli.py shadow.challenge --probe provenance
for op in deploy.final validate.final shadow.replay cutover.check cutover promote submit; do "$PY" env_cli.py "$op"; done
echo "oracle trajectory complete"
