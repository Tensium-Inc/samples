#!/bin/bash
# Entry point the verifier runs.
#
# The grader lives in two places depending on which image is running: the Harbor build mounts
# tests/ at /tests, the HUD build installs it to /opt/grade (root-only). Hardcoding one path
# meant the other silently ran nothing, so resolve it and fail loudly if neither exists.
set -euo pipefail
for g in /opt/grade/grade.py /tests/grade.py; do
  if [ -f "$g" ]; then exec python3 "$g"; fi
done
echo "FATAL: grade.py not found at /opt/grade or /tests" >&2
exit 1
