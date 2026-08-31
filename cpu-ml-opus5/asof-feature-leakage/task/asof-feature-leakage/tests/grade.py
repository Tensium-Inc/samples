#!/usr/bin/env python3
"""Deterministic grade-by-value grader.

  reward = 1.0 iff  terminal_correct AND gate_walk_ok AND forced_recovery_ok AND depth_ok
  (boolean conjunction — partials are diagnostic and never compensate).

Isolation: the terminal is recomputed in a subprocess and candidate code runs in a second,
unprivileged worker. The gate walk, recovery state and deployed snapshot are owned by the
privileged broker under `/var/lib/asof-task`; workspace lookalikes are ignored.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

MIN_TOTAL_ACTIONS = 23
MIN_OBS_ACTIONS = 15
RECOVERY_FAMILY = "stale_cache"
PROBE_TIMEOUT_SEC = 900
STATE_DIR_DEFAULT = "/var/lib/asof-task"


def _authoritative_state_dir() -> Path:
    """Broker-owned state root, pinned so the graded path cannot be redirected.

    The verifier runs as root; while privileged the authoritative location is the
    hard-coded default and no environment variable can move it, so neither the agent nor a
    forged env can point the root-of-trust at a look-alike. A relocation is honoured only
    when running unprivileged (off-host reproduction), never in the graded path.
    """
    if os.geteuid() == 0:
        return Path(STATE_DIR_DEFAULT)
    override = os.environ.get("ASOF_OFFLINE_STATE_DIR")
    return Path(override) if override else Path(STATE_DIR_DEFAULT)


WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
AUTH_STATE = _authoritative_state_dir()
REWARD_PATH = Path(os.environ.get("REWARD_PATH", "/logs/verifier/reward.json"))
ACTIONS = AUTH_STATE / "actions.jsonl"
CANDIDATE = AUTH_STATE / "candidate"
PROBE = Path(__file__).resolve().parent / "terminal_probe.py"

REQUIRED = ["reproduce", "deploy", "validate", "diagnostics.read",
            "recovery.apply", "validate", "promote", "submit"]


def _load_actions() -> List[Dict[str, Any]]:
    if not ACTIONS.exists():
        return []
    out = []
    for line in ACTIONS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
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
    rec = [a for a in _accepted_ops(actions) if a["op"] == "recovery.apply"]
    if not rec or rec[-1].get("payload", {}).get("family") != RECOVERY_FAMILY:
        return False, "recovery_family_mismatch"
    seen_rec, clean_after = False, False
    for a in _accepted_ops(actions):
        if a["op"] == "recovery.apply":
            seen_rec = True
        if seen_rec and a["op"] == "validate" and a.get("payload", {}).get("status") == "clean":
            clean_after = True
    if not clean_after:
        return False, "no_clean_validate_after_recovery"
    return True, "ok"


def _forced_recovery_ok(actions: List[Dict[str, Any]]) -> bool:
    dirty_before = diagnosed = recovered = False
    for a in _accepted_ops(actions):
        p = a.get("payload", {})
        if a["op"] == "validate" and p.get("status") == "dirty":
            dirty_before = True
        if a["op"] == "diagnostics.read":
            diagnosed = True
        if a["op"] == "recovery.apply" and p.get("family") == RECOVERY_FAMILY:
            recovered = True
    return dirty_before and diagnosed and recovered


def _depth_ok(actions: List[Dict[str, Any]]) -> Tuple[bool, str]:
    accepted = [a for a in actions if a.get("accepted")]
    obs = [a for a in accepted if a.get("op_class") == "observation"]
    if len(accepted) < MIN_TOTAL_ACTIONS:
        return False, f"too_few_actions:{len(accepted)}<{MIN_TOTAL_ACTIONS}"
    if len(obs) < MIN_OBS_ACTIONS:
        return False, f"too_few_observations:{len(obs)}<{MIN_OBS_ACTIONS}"
    expected = [("inspect", None), ("profile", None), *[("sample_records", i) for i in range(13)]]
    actual = [(a.get("op"), a.get("payload", {}).get("cursor")) for a in obs[:15]]
    if actual != expected:
        return False, "investigation_sequence_mismatch"
    seq = [a.get("seq") for a in actions]
    if seq != list(range(1, len(actions) + 1)):
        return False, "authoritative_sequence_discontinuous"
    return True, "ok"


def _terminal_correct() -> Tuple[bool, str, Dict[str, Any]]:
    if not CANDIDATE.exists():
        return False, "missing_candidate", {}
    env = os.environ.copy()
    env["WORKSPACE_DIR"] = str(WORKSPACE)
    try:
        proc = subprocess.run([sys.executable, str(PROBE)], cwd=str(WORKSPACE), env=env,
                              text=True, capture_output=True,
                              timeout=PROBE_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired:
        return False, "probe_timeout", {}
    if proc.returncode != 0:
        return False, f"probe_exit:{proc.returncode}:{proc.stderr[-200:]}", {}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return False, f"probe_parse:{exc}", {"stdout": proc.stdout[-400:]}
    return bool(payload.get("healthy")), str(payload.get("reason", "unhealthy")), payload


def grade() -> Dict[str, Any]:
    actions = _load_actions()
    gate_ok, gate_reason = _gate_walk_ok(actions)
    recovery_ok = _forced_recovery_ok(actions)
    depth_ok, depth_reason = _depth_ok(actions)
    term_ok, term_reason, probe = _terminal_correct()
    strict = bool(gate_ok and recovery_ok and depth_ok and term_ok)
    return {
        "reward": 1.0 if strict else 0.0,
        "_diagnostics": {
            "gate_walk_ok": gate_ok,
            "forced_recovery_ok": recovery_ok,
            "depth_ok": depth_ok,
            "terminal_correct": term_ok,
            "n_actions": len(actions),
            "gate_reason": gate_reason,
            "depth_reason": depth_reason,
            "terminal_reason": term_reason,
            "probe": {k: probe.get(k) for k in ("healthy", "reason") if k in probe},
        },
    }


def main() -> None:
    result = grade()
    diagnostics = result.pop("_diagnostics")
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(json.dumps(result, indent=2))
    print(f"Final score: {result['reward']}")
    print(json.dumps({**result, **diagnostics}, indent=2))


if __name__ == "__main__":
    main()
