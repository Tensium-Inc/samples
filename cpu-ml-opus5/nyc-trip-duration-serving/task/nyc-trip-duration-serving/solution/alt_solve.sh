#!/usr/bin/env bash
# Alternate-correct control: same gated walk as the gold, but deploys the structurally
# independent implementation in solution/alt/. Grading behaviour, not the shape of the fix,
# must accept it -- expected reward 1.0.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOLD_DIR="$here/alt" exec bash "$here/solve.sh"
