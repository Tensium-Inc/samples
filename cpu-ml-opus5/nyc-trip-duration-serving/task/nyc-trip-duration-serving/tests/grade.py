#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTH = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/nyc-trip-task"))
ACTIONS = AUTH / "actions.jsonl"
REWARD = Path(os.environ.get("REWARD_PATH", "/logs/verifier/reward.json"))
REQUIRED = ["reproduce", "deploy", "validate", "diagnostics.read", "recovery.apply",
            "validate", "promote", "submit"]


def subsequence(ops: list[str]) -> bool:
    cursor = 0
    for op in ops:
        if cursor < len(REQUIRED) and op == REQUIRED[cursor]:
            cursor += 1
    return cursor == len(REQUIRED)


def main() -> int:
    try:
        actions = [json.loads(line) for line in ACTIONS.read_text().splitlines()]
    except Exception:
        actions = []
    accepted = [a for a in actions if a.get("accepted")]
    ops = [a.get("op") for a in accepted if a.get("op_class") == "operational"]
    observations = [a for a in accepted if a.get("op_class") == "observation"]
    walk = subsequence(ops)
    depth = len(accepted) >= 16 and len(observations) >= 8
    recovery = any(a.get("op") == "recovery.apply" and
                   a.get("payload", {}).get("family") == "schema_registry" for a in accepted)
    proc = subprocess.run([sys.executable, str(HERE / "terminal_probe.py")],
                          capture_output=True, text=True, timeout=600)
    try:
        terminal = json.loads(proc.stdout.splitlines()[-1])
    except Exception:
        terminal = {"healthy": False, "reason": "probe_failure"}
    reward = 1.0 if walk and depth and recovery and terminal.get("healthy") else 0.0
    payload = {"reward": reward, "gate_walk_ok": walk, "depth_ok": depth,
               "forced_recovery_ok": recovery, "terminal": terminal,
               "n_actions": len(actions)}
    REWARD.parent.mkdir(parents=True, exist_ok=True)
    REWARD.write_text(json.dumps({"reward": reward}, indent=2) + "\n")
    print(f"Final score: {reward}")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
