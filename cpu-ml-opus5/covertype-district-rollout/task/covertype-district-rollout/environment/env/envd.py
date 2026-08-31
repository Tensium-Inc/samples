#!/usr/bin/env python3
"""Environment daemon. Runs as root; the agent reaches it only through `env_cli.py`.

Owns everything the agent must not see or forge:
  * the held-out district (readable by root:envdata, never by the agent's uid)
  * the gated state machine and the append-only action log
  * the rate-limited, lossy validation signal
  * the injected failure and the recovery families that clear it

The agent may run exactly this program via sudo and nothing else (see the sudoers rule in
the Dockerfile), so it can ask for a clean/dirty bit but cannot read what produced it.

Agent code is NEVER imported here. It is executed in a subprocess, as the unprivileged
`runner` user, with a timeout -- a hanging or crashing edit yields a state, not a dead
daemon.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import stat as statmod
import subprocess
import sys
import time
from pathlib import Path

ENV_ROOT = Path("/opt/env")          # root:envdata 0750 -- `runner` cannot traverse this
HIDDEN = ENV_ROOT / "hidden"         # held-out district, WITH labels. root-side only.
STATE = ENV_ROOT / "state"           # action log + frozen terminal. root-side only.
DEPLOY = Path("/srv/deploy")         # agent's deployed code: world-READABLE, not writable
HANDOFF = Path("/srv/handoff")       # features out, predictions back
PREDICT_RUNNER = "/usr/local/lib/predict_runner.py"
LOG = STATE / "actions.jsonl"
SM = STATE / "machine.json"
WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
RUNNER = "runner"

VALIDATE_BUDGET_PER_PHASE = 3
SUBPROCESS_TIMEOUT = 900

# The gated chain. Each op names what must already have happened.
REQUIRES = {
    "reproduce": [],
    "deploy": ["reproduce"],
    "validate": ["deploy"],
    # Gated on `deploy`, NOT on `validate`. op_diagnostics already refuses unless a failure is
    # live, so the extra prerequisite added no protection and created a deadlock: the failure
    # fires ON a validate attempt, which returns rejected and therefore never enters ops_done,
    # so a run whose first validate IS the failure could never reach the diagnostics the error
    # message told it to run.
    "diagnostics.read": ["deploy"],
    "recovery.apply": ["diagnostics.read"],
    "promote": ["validate"],
    "canary.start": ["promote"],
    "canary.read": ["canary.start"],
    "cutover.commit": ["canary.read"],
    "submit": ["cutover.commit"],
}
# Observation ops are logged too, so they can count toward the depth floor. They are free and
# unlimited; they simply were not visible to the grader before because env_cli handled them
# locally and they never reached log().
OBSERVATION_OPS = ["inspect", "profile", "sample_records"]

# Two injected failures, one per phase. The family names are NOT shown to the agent and the
# failure codes deliberately do not name their own fix -- previously `STALE_FEATURE_CACHE`
# plus a printed four-item menu made the recovery a multiple-choice question with the answer
# in the error string.
FAILURES = {"E_SERVE_412": "cache_rebuild"}
# Deliberately larger than the number of faults: the pool must not let the agent infer the
# answer from how many options exist. `diagnostics` returns this list, but only once a
# diagnosis has been read -- the menu is earned, and a wrong pick is still charged.
RECOVERY_FAMILIES = ["cache_rebuild", "retrain", "rollback", "reindex",
                     "rebalance_router", "extend_window", "flush_queue", "resize_pool"]
FAILURE_SYMPTOM = {
    "E_SERVE_412": "the serving cache was materialised against a different feature spec than "
                   "the deployment declares; scored batches are being drawn from it",
}


# ----------------------------------------------------------------- state
def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_state() -> dict:
    if SM.exists():
        return json.loads(SM.read_text())
    return {
        "ops_done": [], "phase": 1, "validates_this_phase": 0,
        "status": "ready", "failure": None, "diagnosed": False,
        "deployed_fingerprint": None, "baseline_fingerprint": None,
        "validated_since_recovery": False, "last_validation_clean": None,
        "promoted": False, "submitted": False, "deploy_count": 0,
        "cleared": [], "canary_width": None, "canary_read_widths": [], "cutover": False,
        # Rule T: the cache is a real object the score depends on, not a status flag.
        "cache_fingerprint": None,
    }


def save_state(s: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    SM.write_text(json.dumps(s, indent=2))
    os.chmod(SM, 0o600)


def log(op: str, accepted: bool, detail: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"ts": now(), "op": op, "accepted": accepted,
                            "detail": detail}, sort_keys=True) + "\n")
    os.chmod(LOG, 0o600)


def out(payload: dict) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("accepted", True) else 2


def reject(op: str, reason: str, hint: str = "") -> int:
    log(op, False, {"reason": reason})
    return out({"accepted": False, "op": op, "reason": reason, "hint": hint})


# ----------------------------------------------------------------- helpers
def run_as_runner(script: str, args: list[str]) -> subprocess.CompletedProcess:
    """Execute agent-adjacent code as the unprivileged runner, with a timeout."""
    return subprocess.run(
        ["/usr/local/bin/runas-runner", sys.executable, script, *args],
        capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
    )


def agent_fingerprint() -> str | None:
    """Ask the agent's own code for its configuration fingerprint, in a subprocess."""
    r = subprocess.run(
        ["/usr/local/bin/runas-runner", sys.executable, "-c",
         "import sys;sys.path.insert(0,'/workspace/target');"
         "from covertype import config, model;print(model.fingerprint(config.load()))"],
        capture_output=True, text=True, timeout=120,
    )
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else None


MAX_DEPLOY_BYTES = 256 * 1024 * 1024


def safe_copy_tree(src: Path, dst: Path, skipped: list[str]) -> int:
    """Copy REGULAR FILES ONLY, never following a link. Returns bytes copied.

    `shutil.copytree` is a privilege hole here. Its default is `symlinks=False`, which
    DEREFERENCES links. This snapshot runs as root and the destination is world-readable by
    design, so `covertype/leak -> /opt/env/hidden` has root publish the labelled district at
    mode 644 -- nested inside the package, so it still imports and `deploy` is still accepted.
    That path scored a full pass with a legitimately green gate walk and an unfixed pipeline.
    Nothing is forged; the pipeline is simply never fixed.

    The class, stated so it is not repeated: the more familiar hole is UNTRUSTED CODE DOING
    TRUSTED WORK; this one is TRUSTED CODE CONSUMING UNTRUSTED INPUT. Same room, opposite
    door. `O_NOFOLLOW` makes the check race-free rather than an lstat-then-open guess, and
    every non-regular entry is skipped and reported rather than silently dropped.
    """
    total = 0
    for entry in sorted(src.iterdir(), key=lambda p: p.name):
        rel = entry.relative_to(WORKSPACE)
        if entry.is_symlink():
            skipped.append(f"symlink:{rel}")
            continue
        st = os.lstat(entry)
        if statmod.S_ISDIR(st.st_mode):
            (dst / entry.name).mkdir(parents=True, exist_ok=True)
            total += safe_copy_tree(entry, dst / entry.name, skipped)
            continue
        if not statmod.S_ISREG(st.st_mode):
            skipped.append(f"special:{rel}")
            continue
        if st.st_nlink > 1:
            skipped.append(f"hardlink:{rel}")
            continue
        fd = os.open(str(entry), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with open(fd, "rb", closefd=True) as fin, open(dst / entry.name, "wb") as fout:
                while chunk := fin.read(1 << 20):
                    total += len(chunk)
                    if total > MAX_DEPLOY_BYTES:
                        raise RuntimeError("deployment exceeds the size cap")
                    fout.write(chunk)
        except OSError as e:                                   # ELOOP etc.
            skipped.append(f"unreadable:{rel}:{e.errno}")
    return total


def snapshot_deployment() -> dict:
    """Copy the agent's code + fitted artifact into a root-OWNED, world-readable slot.

    Owned root:root 0755 on purpose. `runner` must be able to read it to execute the
    pipeline, and must not be able to modify it between deployment and scoring.
    """
    if DEPLOY.exists():
        shutil.rmtree(DEPLOY)
    DEPLOY.mkdir(parents=True)
    skipped: list[str] = []
    for rel in ("covertype", "configs", "artifacts"):
        src = WORKSPACE / rel
        if src.is_symlink():
            skipped.append(f"symlink:{rel}")
            continue
        if src.exists():
            (DEPLOY / rel).mkdir(parents=True, exist_ok=True)
            safe_copy_tree(src, DEPLOY / rel, skipped)
    model = DEPLOY / "artifacts" / "model.joblib"
    subprocess.run(["chown", "-R", "root:root", str(DEPLOY)], check=False)
    subprocess.run(["chmod", "-R", "a+rX,go-w", str(DEPLOY)], check=False)
    return {"has_model": model.exists(),
            "model_bytes": model.stat().st_size if model.exists() else 0,
            "skipped_entries": skipped[:20]}


GRADED_CLASSES = [3, 6]


def score(split: str, state: dict | None = None) -> dict:
    """Score the deployed pipeline against a hidden split. ALL of this runs as root.

    The untrusted process gets features and returns predictions. It never receives a label,
    never sees the bars, and nothing it prints is read. The metric is computed here, against
    labels that never leave this process.
    """
    import pandas as pd                                  # noqa: PLC0415
    from sklearn.metrics import (balanced_accuracy_score,  # noqa: PLC0415
                                 recall_score)

    # The serving cache is materialised per feature spec, and scoring reads through it. If the
    # deployment moved and the cache was not rebuilt, there is nothing valid to score -- the
    # fault has to poison the terminal rather than only flip a status flag, or clearing it is
    # cosmetic. It is cleared for good by the recovery: see op_deploy.
    if state is not None and state.get("cache_fingerprint") != state.get("deployed_fingerprint"):
        return {"error": "serving cache is stale for the deployed feature spec"}

    bars = json.loads((ENV_ROOT / "bars.json").read_text())
    src = HIDDEN / ("batch.csv" if split == "batch" else "cache_la_poudre.csv")
    df = pd.read_csv(src)

    # --- hand over FEATURES ONLY -------------------------------------------------------
    HANDOFF.mkdir(parents=True, exist_ok=True)
    out_dir = HANDOFF / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["chown", "runner:runners", str(out_dir)], check=False)
    subprocess.run(["chmod", "0700", str(out_dir)], check=False)
    preds_path = out_dir / "preds.csv"
    if preds_path.exists():
        preds_path.unlink()                              # never grade a stale file

    features = df.drop(columns=["cover_type"])
    feat_path = HANDOFF / "features.csv"
    features.to_csv(feat_path, index=False)
    os.chmod(feat_path, 0o644)

    r = run_as_runner(PREDICT_RUNNER, [])
    if r.returncode != 0:
        return {"error": "prediction_failed", "stderr": r.stderr[-2000:]}
    if not preds_path.exists():
        return {"error": "no predictions written"}

    # --- validate what came back before it is allowed to mean anything -----------------
    try:
        got = pd.read_csv(preds_path)
    except Exception as e:                               # noqa: BLE001
        return {"error": f"unreadable predictions: {e}"}
    if list(got.columns) != ["cover_type_pred"]:
        return {"error": f"unexpected prediction columns: {list(got.columns)}"}
    if len(got) != len(df):
        return {"error": f"prediction length {len(got)} != {len(df)} rows scored"}
    col = pd.to_numeric(got["cover_type_pred"], errors="coerce")
    if col.isna().any():
        return {"error": "predictions contain non-numeric or missing values"}

    y = df["cover_type"]
    pred = col.astype(int)
    rec = recall_score(y, pred, labels=GRADED_CLASSES,
                       average=None, zero_division=0)
    meets = {str(c): bool(v >= bars[str(c)]) for c, v in zip(GRADED_CLASSES, rec)}
    prog_bars = bars.get("progress", {})
    progress = {str(c): bool(v >= prog_bars.get(str(c), 1.1))
                for c, v in zip(GRADED_CLASSES, rec)}

    # District-skill gate. Recall on the two dominant classes is necessary but NOT sufficient:
    # a feature-blind policy that alternates 3/6 by row position clears both recall bars at
    # ~0.5 each while doing no classification -- it never predicts classes 2 or 4 at all.
    # Balanced accuracy (mean per-class recall over every class the district contains) is
    # exactly 1/n_classes for ANY such policy -- here 0.25, because a position-independent
    # predictor's per-class recall equals its predicted frequency for that class and those
    # frequencies sum to 1. A model that genuinely reads the district rises above that floor
    # (gold ~0.30); a spammer cannot. This is what stops the recall bars being gamed.
    bal_acc = float(balanced_accuracy_score(y, pred))
    skill_ok = bool(bal_acc >= bars.get("min_balanced_accuracy", 1.1))
    return {"split": split, "graded_classes": GRADED_CLASSES, "meets": meets,
            "skill_ok": skill_ok, "balanced_accuracy": round(bal_acc, 4),
            "progress": progress, "meets_bars": all(meets.values()) and skill_ok,
            "n": int(len(df))}


# ----------------------------------------------------------------- ops
def op_reproduce(s: dict, _a) -> int:
    fp = agent_fingerprint()
    if fp is None:
        return reject("reproduce", "the pipeline did not import cleanly",
                      "run `python3 scripts/train.py` and fix the error first")
    s["baseline_fingerprint"] = fp
    s["ops_done"].append("reproduce")
    s["status"] = "reproduced"
    save_state(s)
    log("reproduce", True, {"fingerprint": fp})
    return out({"accepted": True, "op": "reproduce", "fingerprint": fp,
                "note": "baseline recorded. edit the pipeline, retrain, then `deploy`."})


def op_deploy(s: dict, _a) -> int:
    fp = agent_fingerprint()
    if fp is None:
        return reject("deploy", "the pipeline did not import cleanly")
    snap = snapshot_deployment()
    if not snap["has_model"]:
        return reject("deploy", "no fitted model at artifacts/model.joblib",
                      "run `python3 scripts/train.py` before deploying")
    s["deployed_fingerprint"] = fp
    s["deploy_count"] = s.get("deploy_count", 0) + 1
    # The first deployment materialises its own cache. The second leaves it stale, which is
    # the fault the agent has to diagnose. Once the rebuild has been applied it is wired into
    # the deploy path, so every later deployment is testable.
    #
    # Freezing the cache at repair time made the fault outlive its own recovery: any config
    # touched afterwards scored as stale forever, so `validate` errored, `diagnostics` said
    # "nothing to diagnose" and `submit` was refused outright. A run that improved its model
    # after recovering was capped below a pass for a plumbing reason -- the opposite of every
    # failure tracing to a reasoning step, and a better model could score below a worse one.
    if s.get("cache_fingerprint") is None or "E_SERVE_412" in s.get("cleared", []):
        s["cache_fingerprint"] = fp
    if "deploy" not in s["ops_done"]:
        s["ops_done"].append("deploy")
    s["status"] = "deployed"
    save_state(s)
    log("deploy", True, {"fingerprint": fp, **snap})
    return out({"accepted": True, "op": "deploy", "fingerprint": fp,
                "validates_left_this_phase": VALIDATE_BUDGET_PER_PHASE - s["validates_this_phase"]})


def op_validate(s: dict, _a) -> int:
    if s["status"] == "blocked":
        return reject("validate", f"serving is blocked: {s['failure']}",
                      "read the diagnostics, then apply the matching recovery family")
    if s["validates_this_phase"] >= VALIDATE_BUDGET_PER_PHASE:
        return reject("validate", "validation budget exhausted for this phase",
                      "validation is rate limited on purpose; it confirms a hypothesis, "
                      "it is not a search tool")

    # The injected failure: the first validation after the candidate has been redeployed
    # hits a feature cache materialised for the previous deployment.
    #
    # Keyed on the deploy COUNT, not on the fingerprint changing. Keying on the fingerprint
    # made the forced recovery reachable only for runs that altered the feature spec or model
    # settings: a run that fixed the pipeline by changing the split alone kept the same
    # fingerprint, never tripped the failure, and could therefore never satisfy `promote` --
    # an unwinnable trajectory for a legitimate route. The count makes the recovery
    # unavoidable regardless of HOW the run fixes things.
    if (s.get("deploy_count", 0) >= 2 and s["failure"] is None
            and "E_SERVE_412" not in s.get("cleared", [])):
        s["failure"] = "E_SERVE_412"
        s["status"] = "blocked"
        s["validates_this_phase"] += 1
        save_state(s)
        log("validate", False, {"failure": "E_SERVE_412"})
        return out({"accepted": False, "op": "validate", "status": "ERROR",
                    "failure": "E_SERVE_412",
                    "message": "serving aborted before scoring; the feature cache does not "
                               "match the deployed feature spec",
                    "hint": "run `env_cli.py diagnostics` then apply the matching recovery"})

    s["validates_this_phase"] += 1
    bundle = score("batch", s)
    if "error" in bundle:
        save_state(s)
        log("validate", False, bundle)
        return out({"accepted": False, "op": "validate", "status": "ERROR", **bundle})

    # Record the deployed model's QUALITY on every accepted validation, best-so-far, root-side.
    # Nothing about it is returned to the agent -- validate still answers clean/dirty only.
    #
    # Why here: real runs routinely end before `promote`. Scoring quality only at promote (and
    # before that, only at submit) leaves the model-quality rungs unreachable, so the reward
    # grades protocol progress alone and stops separating a good model from a bad one. Every
    # run DOES reach validate, so that is where the credit has to attach for the reward to
    # measure the model rather than the paperwork.
    prov = score("full", s)
    if "error" not in prov:
        best = prov
        if (STATE / "provisional.json").exists():
            try:
                old = json.loads((STATE / "provisional.json").read_text())
                # Prefer a skilled model first, then more recall bars -- so a genuine partial
                # solve is not displaced by a later feature-blind spammer that happens to clear
                # more recall bars (its skill_ok is False and it earns no quality credit anyway).
                def _q(b):
                    return (bool(b.get("skill_ok")), sum(b.get("meets", {}).values()))
                if _q(old) > _q(prov):
                    best = old
            except Exception:  # noqa: BLE001
                pass
        (STATE / "provisional.json").write_text(json.dumps(best, sort_keys=True))
        os.chmod(STATE / "provisional.json", 0o600)

    clean = bool(bundle["meets_bars"])
    s["last_validation_clean"] = clean
    # Promotion needs a validation AFTER the recovery, not a clean one. Requiring clean made
    # every wrong configuration unpromotable, so a run holding a confident wrong answer could
    # never submit it and burned its budget retrying -- a truncated trajectory instead of a
    # decided one, which is an invalid failure mode.
    if s.get("cleared"):
        s["validated_since_recovery"] = True
    if "validate" not in s["ops_done"]:
        s["ops_done"].append("validate")
    save_state(s)
    log("validate", True, {"clean": clean, "phase": s["phase"],
                           "fingerprint": s["deployed_fingerprint"]})
    # Lossy on purpose: one global bit, never a per-class breakdown.
    return out({"accepted": True, "op": "validate",
                "status": "clean" if clean else "dirty",
                "validates_left_this_phase":
                    VALIDATE_BUDGET_PER_PHASE - s["validates_this_phase"]})


def op_diagnostics(s: dict, _a) -> int:
    if s["failure"] is None:
        return reject("diagnostics.read", "nothing to diagnose; serving is not in a failed state")
    s["diagnosed"] = True
    if "diagnostics.read" not in s["ops_done"]:
        s["ops_done"].append("diagnostics.read")
    save_state(s)
    log("diagnostics.read", True, {"failure": s["failure"]})
    detail = {"symptom": FAILURE_SYMPTOM[s["failure"]],
              "cache_key_expected": s["deployed_fingerprint"],
              "cache_key_on_disk": s["baseline_fingerprint"]}
    # The failure CODE still does not name its fix -- that part was right, and a code like
    # STALE_FEATURE_CACHE would put the answer in the error string. But withholding the family
    # list as well turned this step into a spelling bee: an unknown name cost nothing, so the
    # cheapest strategy was to enumerate English. One measured rollout spent 49 of its 62 logged
    # actions guessing, with names like `cache_invalidation`, `rematerialize`, `evict`, `purge`
    # -- every one semantically correct. That run had diagnosed the symptom perfectly and simply
    # could not produce the token.
    #
    # The list is now returned here, and only here: `diagnostics` remains the gate, so the menu
    # is earned by diagnosing. What is being tested is whether the agent can map a described
    # symptom onto the right operation, which is a reasoning step. Which token spells it is not.
    return out({"accepted": True, "op": "diagnostics.read", "failure": s["failure"],
                "detail": detail,
                "recovery_families": RECOVERY_FAMILIES,
                "note": "exactly one of these families addresses the symptom above. A wrong one "
                        "costs a validation from this phase's budget."})


def op_recovery(s: dict, a) -> int:
    if not s["diagnosed"]:
        return reject("recovery.apply", "recovery is gated behind a diagnosis",
                      "run `env_cli.py diagnostics` first")
    live = s["failure"]
    known = a.family in RECOVERY_FAMILIES
    if not known or a.family != FAILURES[live]:
        # ONE rule for every incorrect recovery, spelling included. The old code charged a
        # validation for a known-but-wrong family and let an UNKNOWN name through for free,
        # unlimited -- so the cheapest strategy was not to reason about the symptom but to
        # enumerate plausible English until something stuck. That asymmetry is what made the
        # step a lexical search; collapsing it is what makes the choice cost something.
        #
        # The menu rides along on the rejection too, so a run that never read `diagnostics`
        # learns its options after one misfire instead of searching for dozens of turns.
        s["validates_this_phase"] += 1
        s["wrong_recoveries"] = s.get("wrong_recoveries", 0) + 1
        save_state(s)
        log("recovery.apply", False, {"family": a.family, "matched": False, "failure": live,
                                      "known": known})
        return out({"accepted": False, "op": "recovery.apply", "family": a.family,
                    "reason": ("this family does not address the diagnosed symptom" if known
                               else f"no such recovery family: {a.family}"),
                    "recovery_families": RECOVERY_FAMILIES,
                    "cost": "one validation from this phase's budget",
                    "validates_left_this_phase":
                        max(0, VALIDATE_BUDGET_PER_PHASE - s["validates_this_phase"])})
    s["failure"] = None
    s["diagnosed"] = False
    s["cleared"] = [*s.get("cleared", []), live]
    if "recovery.apply" not in s["ops_done"]:
        s["ops_done"].append("recovery.apply")
    s["status"] = "deployed"
    s["phase"] = 2
    # The guessing cost has to CARRY. Charging it to phase 1 alone was nominal: while the
    # failure is open the agent cannot validate anyway, and the phase change reset the
    # counter, so a run could cycle every family and pay nothing. The time spent guessing
    # now comes out of the budget that matters -- the post-recovery validations, which are
    # how a run tests the fix it just deployed. Never all of it: at least one validation
    # always survives, so guessing can cost a run its margin but never brick it.
    spent = min(s.get("wrong_recoveries", 0), VALIDATE_BUDGET_PER_PHASE - 1)
    s["validates_this_phase"] = spent
    s["cache_fingerprint"] = s["deployed_fingerprint"]
    note = ("cache rebuilt against the deployed spec; "
            f"{VALIDATE_BUDGET_PER_PHASE - spent} validation(s) left this phase")
    save_state(s)
    log("recovery.apply", True, {"family": a.family, "matched": True, "failure": live})
    return out({"accepted": True, "op": "recovery.apply", "family": a.family,
                "cleared": s["cleared"], "note": note,
                "validates_left_this_phase":
                    VALIDATE_BUDGET_PER_PHASE - s["validates_this_phase"]})


def op_promote(s: dict, _a) -> int:
    if s["failure"] is not None:
        return reject("promote", "cannot promote while serving is blocked")
    if not s["validated_since_recovery"]:
        # Two different situations reach here and they need different advice. Telling a run
        # that has never seen a failure to "validate after recovering" is a dead end: there is
        # nothing to recover from yet.
        if not s.get("cleared"):
            return reject("promote", "this deployment has not been through a release cycle",
                          "deploy the candidate you want graded and validate it")
        return reject("promote", "the recovered deployment has not been validated",
                      "validate after recovering, then promote what you want graded")
    s["promoted"] = True
    if "promote" not in s["ops_done"]:
        s["ops_done"].append("promote")
    # Score the promoted candidate NOW and record it provisionally. The frozen terminal still
    # only exists at `submit`, and strict_pass still requires the full walk -- but the model
    # QUALITY rungs must not sit behind the trajectory. When they did, runs that never
    # completed the walk could not be paid for the model they built, and the reward graded
    # protocol alone.
    prov = score("full", s)
    if "error" not in prov:
        (STATE / "provisional.json").write_text(json.dumps(prov, sort_keys=True))
        os.chmod(STATE / "provisional.json", 0o600)
    save_state(s)
    log("promote", True, {"fingerprint": s["deployed_fingerprint"]})
    return out({"accepted": True, "op": "promote",
                "note": "promoted. `canary_start` next, then `canary_read` and "
                        "`cutover_commit` before `submit` will freeze anything."})


def serving_health(width: str, state: dict) -> dict:
    """Is the candidate answering for the routed slice? Health only.

    Row count, column, dtype, value domain. It deliberately does NOT compare against the
    held-out labels and is never described to the agent as confirming correctness -- the
    checklist makes advertising a shape check as a hypothesis oracle a BLOCKER. `validate` is
    the only quality signal and that one does compare real values.
    """
    import pandas as pd                                  # noqa: PLC0415

    df = pd.read_csv(HIDDEN / "batch.csv")
    if width == "narrow":
        # The skewed slice: only cover types the incumbent already serves. The graded classes
        # are absent, which is precisely why the narrow canary cannot exercise the rollout.
        df = df[~df["cover_type"].isin(GRADED_CLASSES)].reset_index(drop=True)
    if len(df) == 0:
        return {"ok": False, "error": "routed slice is empty"}
    feats = df.drop(columns=["cover_type"])
    HANDOFF.mkdir(parents=True, exist_ok=True)
    out_dir = HANDOFF / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["chown", "runner:runners", str(out_dir)], check=False)
    subprocess.run(["chmod", "0700", str(out_dir)], check=False)
    preds = out_dir / "preds.csv"
    if preds.exists():
        preds.unlink()
    fp = HANDOFF / "features.csv"
    feats.to_csv(fp, index=False)
    os.chmod(fp, 0o644)
    r = run_as_runner(PREDICT_RUNNER, [])
    if r.returncode != 0 or not preds.exists():
        return {"ok": False, "error": "candidate did not answer for the routed slice"}
    got = pd.read_csv(preds)
    if list(got.columns) != ["cover_type_pred"] or len(got) != len(df):
        return {"ok": False, "error": "malformed prediction vector"}
    return {"ok": True, "rows_served": int(len(df)),
            "cover_types_routed": ("incumbent classes only" if width == "narrow"
                                   else "incumbent classes + the graded classes")}


def op_canary_start(s: dict, _a) -> int:
    if not s["promoted"]:
        return reject("canary.start", "nothing promoted to put behind a canary")
    s["canary_width"] = "narrow"
    s["status"] = "canary"
    if "canary.start" not in s["ops_done"]:
        s["ops_done"].append("canary.start")
    save_state(s)
    log("canary.start", True, {"width": "narrow"})
    return out({"accepted": True, "op": "canary.start", "width": "narrow",
                "note": "candidate is live on a narrow slice. `canary_read` to check it is "
                        "serving, then `cutover_commit`."})


def op_canary_read(s: dict, _a) -> int:
    if s["failure"] is not None:
        return reject("canary.read", f"the canary is blocked: {s['failure']}",
                      "read the diagnostics, then apply the matching recovery family")
    if s["canary_width"] is None:
        return reject("canary.read", "no canary is running")
    # A second injected fault used to fire here. It was removed: L4 requires at least ONE
    # forced failure and the cache fault satisfies it, while diagnosing a second one cost more
    # agent turns than anything else in the task and pushed whole runs past the rollout time
    # limit. Depth has to come out of what the AGENT must reason about, not out of how many
    # scripted recoveries the trajectory contains.
    h = serving_health(s["canary_width"], s)
    if not h["ok"]:
        save_state(s)
        log("canary.read", False, {"width": s["canary_width"], **h})
        return out({"accepted": False, "op": "canary.read", "status": "ERROR", **h})
    s["canary_read_widths"] = sorted({*s.get("canary_read_widths", []), s["canary_width"]})
    if "canary.read" not in s["ops_done"]:
        s["ops_done"].append("canary.read")
    save_state(s)
    log("canary.read", True, {"width": s["canary_width"], **h})
    return out({"accepted": True, "op": "canary.read", "width": s["canary_width"],
                "serving": "healthy", **h,
                "note": "serving health only -- this does not tell you whether the model is "
                        "right. `validate` is the only quality signal."})



def op_cutover(s: dict, _a) -> int:
    if s["failure"] is not None:
        return reject("cutover.commit", f"the canary is blocked: {s['failure']}")
    if not s.get("canary_read_widths"):
        return reject("cutover.commit", "the canary has not been read",
                      "`canary_read` before committing the cutover")
    s["cutover"] = True
    if "cutover.commit" not in s["ops_done"]:
        s["ops_done"].append("cutover.commit")
    save_state(s)
    log("cutover.commit", True, {"fingerprint": s["deployed_fingerprint"]})
    return out({"accepted": True, "op": "cutover.commit",
                "note": "cutover committed. `submit` to freeze and be scored."})


def op_observe(s: dict, a) -> int:
    """Observation ops are LOGGED so they can count toward the grader's depth floor.

    They were handled entirely inside env_cli before, never reaching log(), which made
    MIN_OBS_ACTIONS unsatisfiable by construction. Free and unlimited; the payload is
    computed client-side, this only records that the agent looked.
    """
    name = getattr(a, "family", None) or "inspect"
    if name not in OBSERVATION_OPS:
        return reject("observe", f"unknown observation: {name}")
    if "observe" not in s["ops_done"]:
        s["ops_done"].append("observe")
    save_state(s)
    log("observe", True, {"what": name})
    return out({"accepted": True, "op": "observe", "what": name})


def op_submit(s: dict, _a) -> int:
    if not s["promoted"]:
        return reject("submit", "nothing promoted")
    if not s.get("cutover"):
        return reject("submit", "the cutover has not been committed")
    bundle = score("full", s)
    if "error" in bundle:
        log("submit", False, bundle)
        return out({"accepted": False, "op": "submit", **bundle})
    (STATE / "terminal.json").write_text(json.dumps(bundle, sort_keys=True))
    os.chmod(STATE / "terminal.json", 0o600)
    s["submitted"] = True
    if "submit" not in s["ops_done"]:
        s["ops_done"].append("submit")
    save_state(s)
    log("submit", True, {"fingerprint": s["deployed_fingerprint"]})
    return out({"accepted": True, "op": "submit",
                "note": "terminal frozen by the environment. Nothing further is graded."})


def op_status(s: dict, _a) -> int:
    return out({"accepted": True, "op": "status", "state": {
        "status": s["status"], "phase": s["phase"],
        "ops_done": s["ops_done"], "failure": s["failure"],
        "validates_left_this_phase": VALIDATE_BUDGET_PER_PHASE - s["validates_this_phase"],
        "promoted": s["promoted"], "submitted": s["submitted"],
    }})


OPS = {"reproduce": op_reproduce, "deploy": op_deploy, "validate": op_validate,
       "diagnostics.read": op_diagnostics, "recovery.apply": op_recovery,
       "promote": op_promote, "canary.start": op_canary_start,
       "canary.read": op_canary_read,
       "cutover.commit": op_cutover, "observe": op_observe,
       "submit": op_submit, "status": op_status}


SOCKET_PATH = "/run/envd.sock"


def _dispatch(op: str, family: str | None) -> dict:
    """Run one op and capture what it would have printed. Root-side."""
    import contextlib
    import io

    class _A:
        pass
    a = _A()
    a.family = family

    st = load_state()
    if st["submitted"] and op != "status":
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            reject(op, "the trajectory is already submitted and frozen")
        return json.loads(buf.getvalue())
    for need in REQUIRES.get(op, []):
        if need not in st["ops_done"]:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                reject(op, f"gate not satisfied: `{need}` must succeed first",
                       f"required order: {' -> '.join(REQUIRES)}")
            return json.loads(buf.getvalue())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        OPS[op](st, a)
    return json.loads(buf.getvalue())


def serve() -> int:
    """Run as a root daemon on a unix socket.

    The socket -- not setuid -- is the privilege boundary. HUD drops the agent's shell with
    `setpriv --no-new-privs`, which permanently disables setuid, so `sudo` can never
    elevate there and a sudo-based control path is simply unreachable. A root daemon
    listening on a socket the agent may connect to works under any sandbox.
    """
    import socket
    import threading

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    # 0660 root:ubuntu -- the agent may drive the ops; `runner` (which executes agent code
    # during scoring) is deliberately NOT able to connect.
    os.chmod(SOCKET_PATH, 0o660)
    try:
        import grp
        os.chown(SOCKET_PATH, 0, grp.getgrnam("ubuntu").gr_gid)
    except Exception:  # noqa: BLE001
        pass
    srv.listen(16)

    def handle(conn):
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data.decode() or "{}")
            op = req.get("op")
            if op not in OPS:
                resp = {"accepted": False, "reason": f"unknown op: {op}"}
            else:
                resp = _dispatch(op, req.get("family"))
            conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception as e:  # noqa: BLE001
            try:
                conn.sendall((json.dumps({"accepted": False, "reason": str(e)}) + "\n").encode())
            except Exception:  # noqa: BLE001
                pass
        finally:
            conn.close()

    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=sorted(OPS) + ["serve"])
    ap.add_argument("--family", default=None)
    a = ap.parse_args()

    if os.geteuid() != 0:
        print(json.dumps({"accepted": False, "reason": "envd must run as root"}))
        return 2

    if a.op == "serve":
        return serve()

    s = load_state()
    if s["submitted"] and a.op != "status":
        return reject(a.op, "the trajectory is already submitted and frozen")

    for need in REQUIRES.get(a.op, []):
        if need not in s["ops_done"]:
            return reject(a.op, f"gate not satisfied: `{need}` must succeed first",
                          f"required order: {' -> '.join(REQUIRES)}")
    return OPS[a.op](s, a)


if __name__ == "__main__":
    raise SystemExit(main())
