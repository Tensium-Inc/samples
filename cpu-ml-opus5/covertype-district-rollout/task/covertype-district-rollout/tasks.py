"""Tasks for covertype-district-rollout.

    hud eval tasks.py <model-id> --gateway --max-steps 60
"""
from env import district_rollout

tasks = [district_rollout()]
