#!/usr/bin/env python3
"""Deterministic by-value grader for the authoritative incident terminal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/post_pci_retrieval"))
REWARD_PATH = Path(os.environ.get("REWARD_PATH", "/logs/verifier/reward.json"))
PROBE = HERE / "terminal_probe.py"
OBSERVATION_SEQUENCE = [
    "inspect.sources", "inspect.schema", "audit.query", "profile.corpus", "sample.records", "test.visible",
]
OPERATION_SEQUENCE = [
    "reproduce", "deploy", "validate.runtime", "diagnostics.read", "recovery.apply", "validate.runtime",
    "audit.provenance", "audit.lexical", "audit.fusion", "deploy.ranker", "validate.ranker",
    "audit.balance", "shadow.challenge", "shadow.challenge", "deploy.final", "validate.final", "shadow.replay",
    "cutover.check", "cutover", "promote", "submit",
]
REQUIRED = OBSERVATION_SEQUENCE + OPERATION_SEQUENCE
OBSERVATIONS = set(OBSERVATION_SEQUENCE)


def _actions() -> list[dict[str, Any]]:
    path = STATE_DIR / "accepted_actions.jsonl"
    if not path.exists(): return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip(): out.append(json.loads(line))
    return out


def _walk(actions: list[dict[str, Any]]) -> tuple[bool, str]:
    accepted = [a for a in actions if a.get("accepted")]
    ops = [a.get("op") for a in accepted]
    cursor = 0
    for op in ops:
        if cursor < len(REQUIRED) and op == REQUIRED[cursor]: cursor += 1
    if cursor != len(REQUIRED): return False, f"missing_gate_progress:{cursor}/{len(REQUIRED)}"
    if len(accepted) < len(REQUIRED): return False, f"accepted_depth_below_{len(REQUIRED)}"
    observed = {a.get("op") for a in accepted if a.get("op") in OBSERVATIONS}
    if observed != OBSERVATIONS: return False, "observation_set_incomplete"
    for action in accepted:
        expected_class = "observation" if action.get("op") in OBSERVATIONS else "operational"
        if action.get("op_class", "operational") != expected_class:
            return False, f"op_class_mismatch:{action.get('op')}"
    seqs = [a.get("seq") for a in actions]
    if seqs != list(range(1, len(actions)+1)): return False, "authoritative_sequence_discontinuous"
    return True, "ok"


def _recovery(actions: list[dict[str, Any]]) -> tuple[bool, str]:
    accepted = [a for a in actions if a.get("accepted")]
    dirty = any(a.get("op") == "validate.runtime" and a.get("payload", {}).get("status") == "dirty" for a in accepted)
    diagnosed = any(a.get("op") == "diagnostics.read" and a.get("payload", {}).get("family") == "stale_vector_cache" for a in accepted)
    recovered = any(a.get("op") == "recovery.apply" and a.get("payload", {}).get("family") == "stale_vector_cache" for a in accepted)
    return dirty and diagnosed and recovered, "ok" if dirty and diagnosed and recovered else "forced_recovery_incomplete"


def _terminal() -> tuple[bool, str, dict[str, Any]]:
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "TASK_STATE_DIR", "PROBE_CANDIDATE_UID", "PROBE_CANDIDATE_GID", "PROBE_ITEM_TIMEOUT_SEC"}}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    try:
        proc = subprocess.run([sys.executable, str(PROBE)], cwd=HERE, env=env, text=True,
                              capture_output=True, timeout=360, check=False)
    except subprocess.TimeoutExpired:
        return False, "terminal_probe_timeout", {}
    if proc.returncode != 0: return False, f"terminal_probe_exit_{proc.returncode}", {"stderr": proc.stderr[-200:]}
    try: payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc: return False, f"terminal_probe_json:{exc}", {"stdout": proc.stdout[-300:]}
    return bool(payload.get("healthy")), str(payload.get("reason", "unhealthy")), payload


def grade() -> dict[str, Any]:
    actions = _actions()
    walk_ok, walk_reason = _walk(actions)
    recovery_ok, recovery_reason = _recovery(actions)
    try: state = json.loads((STATE_DIR / "state.json").read_text())
    except Exception: state = {}
    state_ok = bool(state.get("submitted") and state.get("shadow_stable") and state.get("ranker_clean")
                    and state.get("final_clean") and state.get("cutover_ready")
                    and not state.get("recovery_locked"))
    terminal_ok, terminal_reason, terminal = _terminal()
    strict = bool(walk_ok and recovery_ok and state_ok and terminal_ok)
    return {"reward": 1.0 if strict else 0.0, "detail": {
        "gate_walk_ok": walk_ok, "gate_reason": walk_reason,
        "forced_recovery_ok": recovery_ok, "recovery_reason": recovery_reason,
        "authoritative_state_ok": state_ok, "terminal_correct": terminal_ok,
        "terminal_reason": terminal_reason, "accepted_actions": sum(bool(a.get("accepted")) for a in actions),
        "total_receipts": len(actions), "strict_pass": strict,
        "terminal": {k: terminal.get(k) for k in (
            "healthy", "reason", "scenario_count", "terminal_hash", "isolation_checks"
        ) if k in terminal},
    }}


def main() -> None:
    result = grade()
    detail = result["detail"]
    reward = {"reward": float(result["reward"])}

    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(
        json.dumps(reward, indent=2, sort_keys=True) + "\n"
    )
    (REWARD_PATH.parent / "detail.json").write_text(
        json.dumps(detail, indent=2, sort_keys=True) + "\n"
    )

    print(f"Final score: {reward['reward']}")
    print(json.dumps(detail, indent=2, sort_keys=True))


if __name__ == "__main__": main()
