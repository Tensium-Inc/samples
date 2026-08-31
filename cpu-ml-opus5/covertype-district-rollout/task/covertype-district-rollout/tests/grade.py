#!/usr/bin/env python3
"""Grader: replay the accepted-action log, grade the environment-owned terminal by value.

    strict_pass = terminal_correct AND gate_walk_ok AND forced_recovery_ok

A boolean conjunction. Reward is 1.0 only on the full conjunction; anything short of it
gets a diagnostic partial that is capped well below 1.0 and never compensates. That is what
makes a correct fix submitted through a minimal path fall short.

Isolation notes (these are graded requirements, not style):
  * The agent's code is never imported here. It was executed by the environment, in a
    subprocess, as an unprivileged user, at `submit` time. The grader only reads the frozen
    result.
  * The expected terminal is stored as a salted hash rather than in plaintext. That is a
    tamper check, NOT secrecy: the salt sits in this file and the canonical bundle spans only
    a handful of states, so anyone holding this file can enumerate it in about one guess. It
    does not matter here (the bars are stated in the prompt and terminal.json is root 0600, so
    there is nothing to gain) but the property must not be overclaimed, and a task whose
    terminal carried real information would need a different mechanism.
  * The action log and the terminal are root-owned under /opt/env/state and were never
    writable by the agent, so the trajectory cannot be forged after the fact.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

# ENV_STATE_DIR lets _private/mutation_check.py re-grade a captured trajectory offline.
# In the container it is unset and the state is read from its root-owned home.
STATE = Path(os.environ.get("ENV_STATE_DIR", "/opt/env/state"))
LOG = STATE / "actions.jsonl"
MACHINE = STATE / "machine.json"
TERMINAL = STATE / "terminal.json"
PROVISIONAL = STATE / "provisional.json"
REWARD_PATH = Path(os.environ.get("VERIFIER_LOG_DIR", "/logs/verifier")) / "reward.json"

# Per-instance salt + the hash of the canonical terminal bundle. The plaintext bundle
# appears nowhere in this repository.
TERMINAL_SALT = "covt-district-rollout::a9f13c7e2b5d"
EXPECTED_TERMINAL_SHA256 = "866f6b48dc572163c4e99399c55c057a69174710d11128f543b5c05da99aa6c4"

# The ordered walk. Every one of these must appear, accepted, in this relative order.
#
# `validate` sits AFTER the recovery, which is the validation that matters and the one
# `promote` already requires. Putting it before `diagnostics.read` penalised a legitimate
# route: the failure fires ON a validate attempt and that attempt is logged as rejected, so a
# run whose first validate IS the failure had no accepted `validate` to match before
# diagnosing, and lost the walk credit for a plumbing reason. Matching is by subsequence, so a
# run that also validates early still matches.
REQUIRED_WALK = ["reproduce", "deploy", "diagnostics.read", "recovery.apply", "validate",
                 "promote", "canary.start", "canary.read", "cutover.commit", "submit"]
# The injected failure must fire, be diagnosed, and be cleared by the family that addresses
# it. The code deliberately does not name its own fix.
REQUIRED_RECOVERIES = {"E_SERVE_412": "cache_rebuild"}

# Depth floors, per the Category 02 template. The task previously had none at all, so nothing
# stopped a run clearing the walk with the bare minimum. Observation ops are logged by the
# daemon, which is what makes MIN_OBS_ACTIONS satisfiable rather than dead.
#
# The two floors are disjoint conditions, and a control that fails BOTH proves NEITHER is
# load-bearing. Each is witnessed by a control that clears the other:
#   nc_no_investigation  15 accepted /  2 observations -> action floor cleared, obs floor fails
#   nc_wrong_recovery    12 accepted /  6 observations -> obs floor cleared, action floor fails
# Both are in _private/mutation_check.py's CAPTURE set for exactly that reason.
MIN_TOTAL_ACTIONS = 15
MIN_OBS_ACTIONS = 4
# ...of at least this many DISTINCT kinds. Every observation logs as op "observe" with the
# kind in detail.what, so a count alone was satisfiable by repeating one call four times --
# which is not investigation. Gold uses inspect + profile + sample_records.
MIN_OBS_KINDS = 3
OBSERVATION_OPS = {"observe"}

# Diagnostic partials. They exist to make a failed run legible; they cannot reach 1.0.
#
# There is deliberately NO separate weight for "a terminal was frozen". A frozen terminal
# structurally implies the whole walk and the recovery -- `submit` sits behind
# `cutover.commit` -> `canary.read` -> `promote` (which needs a validation after E_SERVE_412
# was cleared) -- so such a weight could never be paid out on its own, and it made the cheat
# ceiling numerically equal to terminal + walk, which meant the ceiling could not tell "cheat
# blocked" from "cheat obtained the answer".
#
# A dense, ORDERED ladder. An earlier ladder had rungs that real runs reached all at once or
# not at all, which is a flat floor rather than a gradient, and a flat floor cannot separate
# models. Each rung below is independently reachable and marks how far the reasoning got.
W_START = 0.05          # reproduce + a first deploy accepted
W_WALK = 0.10           # the full ordered walk
W_REC1 = 0.20           # the forced failure diagnosed and cleared
W_DEPTH = 0.05          # investigated enough to satisfy the floors
W_PROGRESS = 0.15       # moved a graded class meaningfully above the shipped baseline
W_BAR_EACH = 0.12       # per graded bar cleared -- cumulative, so two > one
PARTIAL_CAP = 0.70


def canonical(bundle: dict) -> str:
    """The graded shape of the terminal: which recall bars the district run cleared AND
    whether the promoted model actually classified the district (balanced accuracy above the
    no-skill floor) rather than gaming the two recall bars with a feature-blind label pattern.
    `skill_ok` defaults to False so a pre-fix bundle that predates the gate can never hash to
    the correct terminal."""
    return json.dumps({"graded_classes": bundle["graded_classes"],
                       "meets": bundle["meets"],
                       "skill_ok": bundle.get("skill_ok", False)}, sort_keys=True)


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def check_walk(log: list[dict]) -> tuple[bool, list[str]]:
    """Every required op accepted, in order. Extra ops between them are fine."""
    accepted = [e["op"] for e in log if e.get("accepted")]
    seen, i = [], 0
    for op in accepted:
        if i < len(REQUIRED_WALK) and op == REQUIRED_WALK[i]:
            seen.append(op)
            i += 1
    return i == len(REQUIRED_WALK), seen


def check_recovery(log: list[dict]) -> tuple[bool, str, dict]:
    """Every injected failure must have fired, been diagnosed, and been cleared by the family
    that addresses it, each recovery after its own diagnosis."""
    per, reasons = {}, []
    for failure, family in REQUIRED_RECOVERIES.items():
        fired_at = next((i for i, e in enumerate(log)
                         if not e.get("accepted")
                         and e.get("detail", {}).get("failure") == failure), None)
        if fired_at is None:
            per[failure] = False
            reasons.append(f"{failure} never fired")
            continue
        diag_at = next((i for i, e in enumerate(log[fired_at:], start=fired_at)
                        if e["op"] == "diagnostics.read" and e.get("accepted")), None)
        if diag_at is None:
            per[failure] = False
            reasons.append(f"{failure} was never diagnosed")
            continue
        per[failure] = any(e["op"] == "recovery.apply" and e.get("accepted")
                           and e.get("detail", {}).get("family") == family
                           for e in log[diag_at:])
        if not per[failure]:
            reasons.append(f"{failure} was never cleared by the matching family")
    return all(per.values()), "; ".join(reasons), per


def check_depth(log: list[dict]) -> tuple[bool, str, dict]:
    """Floors on total accepted actions and on observation actions."""
    accepted = [e for e in log if e.get("accepted")]
    n_total = len(accepted)
    obs = [e for e in accepted if e["op"] in OBSERVATION_OPS]
    n_obs = len(obs)
    n_kinds = len({e.get("detail", {}).get("what") for e in obs})
    msgs = []
    if n_total < MIN_TOTAL_ACTIONS:
        msgs.append(f"{n_total} accepted actions < {MIN_TOTAL_ACTIONS}")
    if n_obs < MIN_OBS_ACTIONS:
        msgs.append(f"{n_obs} observation actions < {MIN_OBS_ACTIONS}")
    if n_kinds < MIN_OBS_KINDS:
        msgs.append(f"{n_kinds} distinct observation kinds < {MIN_OBS_KINDS}")
    return not msgs, "; ".join(msgs), {"accepted": n_total, "observations": n_obs,
                                      "observation_kinds": n_kinds}


def check_terminal() -> tuple[dict | None, bool, str]:
    if not TERMINAL.exists():
        return None, False, "no terminal was frozen (the run never reached `submit`)"
    bundle = json.loads(TERMINAL.read_text())
    got = hashlib.sha256((TERMINAL_SALT + canonical(bundle)).encode()).hexdigest()
    ok = got == EXPECTED_TERMINAL_SHA256
    return bundle, ok, "" if ok else "the promoted candidate did not clear both bars"


def main() -> int:
    log = read_log()
    walk_ok, walk_seen = check_walk(log)
    rec_ok, rec_why, rec_per = check_recovery(log)
    depth_ok, depth_why, depth_counts = check_depth(log)
    bundle, term_ok, term_why = check_terminal()

    strict = term_ok and walk_ok and rec_ok and depth_ok
    parts: dict[str, float] = {}
    accepted_ops = [e["op"] for e in log if e.get("accepted")]
    if "reproduce" in accepted_ops and "deploy" in accepted_ops:
        parts["started"] = W_START
    if walk_ok:
        parts["gate_walk"] = W_WALK
    if rec_per.get("E_SERVE_412"):
        parts["recovered_E_SERVE_412"] = W_REC1
    if depth_ok:
        parts["depth"] = W_DEPTH
    # Quality credit comes from the frozen terminal when there is one, otherwise from the
    # provisional score taken at `promote`. strict_pass is unaffected -- it still requires the
    # terminal, the full walk and the forced recovery -- but a run that promoted a genuinely
    # better model is now paid for it even if it never reached the cutover.
    quality = bundle
    if quality is None and PROVISIONAL.exists():
        try:
            quality = json.loads(PROVISIONAL.read_text())
        except Exception:  # noqa: BLE001
            quality = None
    # Quality rungs are paid only to a model that cleared the district-skill gate. Without this
    # guard a feature-blind 3/6 spammer -- which clears both recall bars and both `progress`
    # marks -- would collect moved_a_class + bars_cleared (0.15 + 0.24) and bank the 0.70 cap
    # for doing no classification. Gating on skill_ok drops such a run to the protocol floor
    # (0.40) and reserves the quality gradient for models that actually read the district.
    if quality and quality.get("skill_ok"):
        meets = quality.get("meets", {})
        progress = quality.get("progress", {})
        n_bars = sum(1 for v in meets.values() if v)
        if any(progress.values()) or n_bars:
            parts["moved_a_class"] = W_PROGRESS
        if n_bars:
            parts["bars_cleared"] = round(W_BAR_EACH * n_bars, 4)

    reward = 1.0 if strict else round(min(PARTIAL_CAP, sum(parts.values())), 4)

    info = {
        "strict_pass": strict,
        "terminal_correct": term_ok,
        "gate_walk_ok": walk_ok,
        "forced_recovery_ok": rec_ok,
        "recoveries": rec_per,
        "depth_ok": depth_ok,
        "depth": depth_counts,
        "partial_components": parts,
        "walk_seen": walk_seen,
        "required_walk": REQUIRED_WALK,
        "accepted_actions": sum(1 for e in log if e.get("accepted")),
        "rejected_actions": sum(1 for e in log if not e.get("accepted")),
        "reasons": [r for r in (term_why, rec_why, depth_why) if r],
    }
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(json.dumps({"reward": reward}))
    print(f"Final score: {reward}")
    print(json.dumps({"reward": reward, "info": info}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
