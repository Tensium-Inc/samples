#!/bin/bash
# Oracle / gold driver = the POSITIVE CONTROL for covertype-district-rollout.
#
# Applies the correct fix AND drives the full gated trajectory to `submit`. It must score
# strict_pass = true, reward 1.0. If it does not, the op surface, the grader or the
# workspace is mis-specified -- fix that before trusting any other number.
#
# The fix has two parts. `_private/basin_sweep.py` measures which, and it corrected an earlier
# claim in this file: dropping the SOIL codes is optional -- terrain+soil passes at alpha 1.2,
# 1.5 and 1.8, and no bar setting exists that makes soil necessary. What is actually required:
#
#   1. drop the WILDERNESS-AREA one-hots. They are the training districts' identity, and the
#      new district is a fourth value the model has never seen, so they carry no transferable
#      signal -- only a shortcut the model leans on.
#   2. weight the classes, and keep the weight BOUNDED. The new district's cover-type mix is
#      close to inverted relative to training, so an unweighted fit optimises a prior that does
#      not hold there. alpha in roughly [1.0, 1.8] clears both bars; alpha 2.0 collapses the
#      model onto a single class (r3 0.000, r6 1.000) and the conjunction of the two bars
#      catches it. The answer is a bounded region, not a direction to push in.
#
# This oracle also drops the soil codes. That is harmless and it is what the frozen terminal
# was calibrated on; it is NOT required, and the write-up no longer claims it is.


set -euo pipefail
WORKSPACE="${WORKSPACE_DIR:-/workspace/target}"
cd "$WORKSPACE"

for f in configs/pipeline.yaml scripts/train.py env_cli.py covertype/features.py covertype/model.py; do
  [ -f "$f" ] || { echo "FATAL: missing expected file: $f" >&2; exit 1; }
done

step() { echo "[solve] $*"; }

# This is ONE route to 1.0, not the only one. `_private/negative_controls/pc_*.sh` drive the
# other legitimate routes -- fixing before the first deploy, recovering before fixing, and
# tuning again after the recovery -- and each of them must also score 1.0. Any change to the
# op surface has to be checked against those, not only against this file, because gold's
# particular order can hide a gate that blocks every other order.

step "look around -- observation is free, unlimited, and recorded for the depth floor"
python3 env_cli.py inspect           >/dev/null
python3 env_cli.py profile           >/dev/null
python3 env_cli.py sample_records 5  >/dev/null
python3 env_cli.py sample_records 25 >/dev/null

step "baseline: train the pipeline exactly as inherited"
python3 scripts/train.py --quiet

step "reproduce (records the baseline fingerprint)"
python3 env_cli.py reproduce >/dev/null

step "deploy the untouched baseline and see where it stands"
python3 env_cli.py deploy >/dev/null
python3 env_cli.py validate | grep -E '"status"' || true

step "apply the fix: drop location identity, weight the classes"
python3 - <<'PY'
import yaml
p = "configs/pipeline.yaml"
c = yaml.safe_load(open(p))
c["features"]["soil_type"] = False        # soil codes are district identity, not terrain
c["features"]["wilderness_area"] = False  # ditto, and the new district is a fourth value
c["model"]["class_weight_alpha"] = 1.5    # bounded: 2.0 collapses onto one class
yaml.safe_dump(c, open(p, "w"), sort_keys=False)
PY

step "retrain on the corrected spec"
python3 scripts/train.py --quiet

step "deploy the fix"
python3 env_cli.py deploy >/dev/null

step "validate -> the deployment trips the stale serving cache"
python3 env_cli.py validate | grep -E '"failure"' || true

step "read the diagnostics"
python3 env_cli.py diagnostics | grep -E '"failure"' || true

step "apply the recovery family that matches the diagnosed symptom"
python3 env_cli.py recovery cache_rebuild >/dev/null

step "validate the recovered deployment"
python3 env_cli.py validate | grep -E '"status"' || true

step "promote the validated candidate"
python3 env_cli.py promote >/dev/null

step "start the canary on a narrow slice"
python3 env_cli.py canary_start >/dev/null


step "read the narrow canary again, now that the graded classes route"
python3 env_cli.py canary_read | grep -E '"serving"' || true

step "commit the cutover, then freeze"
python3 env_cli.py cutover_commit >/dev/null
python3 env_cli.py submit >/dev/null

step "done -- trajectory complete"
