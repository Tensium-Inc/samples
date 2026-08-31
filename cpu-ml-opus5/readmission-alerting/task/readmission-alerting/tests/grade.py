#!/usr/bin/env python3
"""Deterministic grader. No model, no network.

    reward = 1.0 iff terminal_correct AND gate_walk_ok AND forced_recovery_ok

A boolean conjunction: partial progress is reported for diagnosis but never scores.

Trajectory depth is measured and reported, but is NOT a scoring term. An action count is a
proxy that a candidate can pad and a correct solve can miss, and scoring on it makes a
correct solve indistinguishable from a do-nothing stub. Depth is instead forced by
construction: the prepared state restored at every deploy keeps the published figure at the
pre-change value until both staged faults are diagnosed and cleared, so a candidate that
never drives the service cannot publish an honest figure at all.

Isolation:
  * Terminal correctness is measured in a SUBPROCESS (terminal_probe.py). Submitted code is
    never imported into this process, so a hanging or crashing candidate yields a score
    rather than a dead grader.
  * The gate walk and forced-recovery checks are read from a log that lives inside the
    submitted tree and are therefore corroborating only. `terminal_correct` is what carries
    the score.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Thresholds.
# ---------------------------------------------------------------------------

# Reported for diagnosis only - see the module docstring. Not scoring terms.
MIN_TOTAL_ACTIONS = 14      # trajectory depth we expect a genuine solve to reach
MIN_OBS_ACTIONS = 6         # observation depth we expect a genuine solve to reach
RECOVERY_FAMILIES = ["stale_matrix", "stale_report"]   # must equal env_cli FAULTS, in order
PROBE_TIMEOUT_SEC = 600

# ---------------------------------------------------------------------------

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
REWARD_PATH = Path(os.environ.get("REWARD_PATH", "/logs/verifier/reward.json"))
# Ledger and candidate snapshot are owned by the root daemon, outside the agent tree.
AUTH = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/readmission-task"))
ACTIONS = AUTH / "actions.jsonl"
CANDIDATE = AUTH / "candidate"
PROBE = Path(__file__).resolve().parent / "terminal_probe.py"

REQUIRED = ["reproduce", "deploy", "validate",
            "diagnostics.read", "recovery.apply", "validate",
            "diagnostics.read", "recovery.apply", "validate",
            "promote", "submit"]


def _load_actions() -> List[Dict[str, Any]]:
    if not ACTIONS.exists():
        return []
    out = []
    for line in ACTIONS.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _accepted_ops(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [a for a in actions
            if a.get("accepted") and a.get("op_class", "operational") != "observation"]


def _gate_walk_ok(actions: List[Dict[str, Any]]) -> Tuple[bool, str]:
    ops = [a["op"] for a in _accepted_ops(actions)]
    i = 0
    for op in ops:
        if i < len(REQUIRED) and op == REQUIRED[i]:
            i += 1
    if i != len(REQUIRED):
        return False, f"missing_gate_progress:{i}/{len(REQUIRED)}"
    rec_fams = [a.get("payload", {}).get("family")
                for a in _accepted_ops(actions) if a["op"] == "recovery.apply"]
    for fam in RECOVERY_FAMILIES:
        if fam not in rec_fams:
            return False, f"recovery_family_missing:{fam}"
    n_rec, clean_after_all = 0, False
    for a in _accepted_ops(actions):
        if a["op"] == "recovery.apply":
            n_rec += 1
        if (n_rec >= len(RECOVERY_FAMILIES) and a["op"] == "validate"
                and a.get("payload", {}).get("status") == "clean"):
            clean_after_all = True
    if not clean_after_all:
        return False, "no_clean_validate_after_recovery"
    return True, "ok"


def _forced_recovery_ok(actions: List[Dict[str, Any]]) -> bool:
    """Each accepted recovery must be evidence-driven: a diagnostics.read after the
    previous recovery and before this one. Diagnostics reports evidence, not the family;
    the service only accepts the family matching the outstanding fault, so an accepted
    recovery.apply IS the family check."""
    dirty_before = False
    fresh_diagnosis = False
    recovered: List[str] = []
    for a in _accepted_ops(actions):
        p = a.get("payload", {})
        if a["op"] == "validate" and p.get("status") == "dirty":
            dirty_before = True
        if a["op"] == "diagnostics.read":
            fresh_diagnosis = True
        if a["op"] == "recovery.apply" and p.get("family"):
            if not fresh_diagnosis:
                return False
            recovered.append(p["family"])
            fresh_diagnosis = False
    return dirty_before and recovered == RECOVERY_FAMILIES


def _depth_ok(actions: List[Dict[str, Any]]) -> Tuple[bool, str]:
    accepted = [a for a in actions if a.get("accepted")]
    obs = [a for a in accepted if a.get("op_class") == "observation"]
    if len(accepted) < MIN_TOTAL_ACTIONS:
        return False, f"too_few_actions:{len(accepted)}<{MIN_TOTAL_ACTIONS}"
    if len(obs) < MIN_OBS_ACTIONS:
        return False, f"too_few_observations:{len(obs)}<{MIN_OBS_ACTIONS}"
    return True, "ok"


def _terminal_correct() -> Tuple[bool, str, Dict[str, Any]]:
    if not CANDIDATE.exists():
        return False, "missing_candidate", {}
    env = os.environ.copy()
    env["WORKSPACE_DIR"] = str(WORKSPACE)
    try:
        proc = subprocess.run([sys.executable, str(PROBE)], cwd=str(WORKSPACE), env=env,
                              text=True, capture_output=True, timeout=PROBE_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired:
        return False, "probe_timeout", {}
    if proc.returncode != 0:
        return False, f"probe_exit:{proc.returncode}:{proc.stderr[-200:]}", {}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return False, f"probe_parse:{exc}", {"stdout": proc.stdout[-400:]}
    return bool(payload.get("healthy")), str(payload.get("reason", "unhealthy")), payload


def grade() -> Dict[str, Any]:
    actions = _load_actions()
    gate_ok, gate_reason = _gate_walk_ok(actions)
    recovery_ok = _forced_recovery_ok(actions)
    depth_ok, depth_reason = _depth_ok(actions)
    term_ok, term_reason, probe = _terminal_correct()
    strict = bool(gate_ok and recovery_ok and term_ok)
    return {
        "reward": 1.0 if strict else 0.0,
        "detail": {
            "gate_walk_ok": gate_ok, "gate_reason": gate_reason,
            "forced_recovery_ok": recovery_ok,
            "depth_ok": depth_ok, "depth_reason": depth_reason,   # diagnostic only
            "terminal_correct": term_ok, "terminal_reason": term_reason,
            "n_actions": len(actions),
            "probe": {k: probe.get(k) for k in ("healthy", "reason", "recovery_ok", "verifier_dir") if k in probe},
            "strict_pass": strict,
        },
    }


def main() -> None:
    result = grade()
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(json.dumps(result, indent=2))
    print(f"Final score: {result['reward']}")
    print(json.dumps(result["detail"], indent=2))


if __name__ == "__main__":
    main()
