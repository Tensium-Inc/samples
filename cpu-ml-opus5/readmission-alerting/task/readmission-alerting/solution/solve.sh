#!/usr/bin/env bash
# Reference solution: applies the correct fix and drives the full gated trajectory to submit.
# Scores 1.0 through tests/grade.py. The reference source lives outside the candidate tree.
set -euo pipefail
WORKSPACE="${WORKSPACE_DIR:-/workspace/target}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REF_DIR="${REF_DIR:-$SCRIPT_DIR/reference}"
PY="${PYTHON:-python3}"

cd "$WORKSPACE"
rm -rf .envstate

# --- Phase 1: orient. ----------------------------------------------------------------------
"$PY" env_cli.py inspect        >/dev/null
"$PY" env_cli.py profile        >/dev/null

# --- Phase 2: reproduce the published figure. ----------------------------------------------
"$PY" env_cli.py reproduce
"$PY" env_cli.py sample_records >/dev/null
"$PY" env_cli.py profile        >/dev/null

# --- Phase 3: read the cohort and what the config commits to publishing. --------------------
"$PY" env_cli.py inspect        >/dev/null
"$PY" env_cli.py sample_records >/dev/null

# --- Phase 4: both corrections - rates fitted inside each fold, and the documented metric. --
cp "$REF_DIR/load.py" src/load.py
cp "$REF_DIR/features.py" src/features.py
cp "$REF_DIR/evaluate.py" src/evaluate.py

"$PY" env_cli.py inspect        >/dev/null
"$PY" env_cli.py profile        >/dev/null

# --- Phase 5: drive the gated walk. ---------------------------------------------------------
"$PY" env_cli.py deploy
"$PY" env_cli.py validate
"$PY" env_cli.py diagnostics.read
"$PY" env_cli.py recovery.apply --family "stale_matrix"
"$PY" env_cli.py sample_records >/dev/null
"$PY" env_cli.py inspect        >/dev/null
"$PY" env_cli.py validate
"$PY" env_cli.py diagnostics.read
"$PY" env_cli.py recovery.apply --family "stale_report"
"$PY" env_cli.py validate
"$PY" env_cli.py profile        >/dev/null
"$PY" env_cli.py promote
"$PY" env_cli.py submit

echo "reference trajectory complete"
