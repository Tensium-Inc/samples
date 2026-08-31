#!/bin/bash
# Verifier entry point. Deterministic, offline.
set -euo pipefail
exec python3 /tests/grade.py
