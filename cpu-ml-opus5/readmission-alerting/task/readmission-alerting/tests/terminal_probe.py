#!/usr/bin/env python3
"""Terminal measurement. Run as a subprocess by grade.py.

The candidate's job is to make the figure the pipeline publishes a sound estimate of how the
alerting model ranks a rare event. The terminal is that figure, graded by value against a
reference the verifier computes itself.

Four properties keep it sound:
  * Candidate code NEVER runs in this process. Every call into the candidate happens in a
    child interpreter dropped to an unprivileged uid, whose stdout is discarded, returning
    its figure through a RESULT FILE. A candidate can report whatever number it likes - that
    is its job - but it cannot pre-empt or forge the verdict.
  * The reference is computed here, from the encounter table directly, with the verifier's
    own fold-wise encoding and the documented policy metric. It never calls the candidate.
  * Graded by value with a tolerance, so any sound implementation passes and a structural
    imitation does not.
  * The measurement is repeated on a re-balanced copy of the cohort, where the reference
    moves. A figure that is computed follows it; one that is emitted does not.

Prints one JSON line.
"""
from __future__ import annotations

import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

# ---------------------------------------------------------------------------

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
# Candidate snapshot is owned by the root daemon, outside the agent tree.
AUTH = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/readmission-task"))
CANDIDATE = AUTH / "candidate"
STAGED_CACHE = WORKSPACE / "caches" / "design_matrix.pkl"
SUMMARY_MEMO = WORKSPACE / "caches" / "eval_summary.pkl"
STAGED_ARTIFACTS = ("design_matrix.pkl", "eval_summary.pkl")
RELEASE_STATE = Path(__file__).resolve().parent / "release_state.json"
FROZEN_CONFIG = Path(__file__).resolve().parent / "config_frozen.yaml"

# Tolerance on the published figure. Measured ladder (see _private/measure.py):
#     correct implementations        |gap| <= 0.0054  (inner seed, stratified, smoothing)
#     encoding not nested             0.0391
#     cohort keeps unobservable rows  0.076
#     metric fixed, encoding leaking  0.13
#     encoding fixed, metric wrong    0.55
#     nothing fixed                   0.56
TOL = 0.012

# Condition B re-balances the cohort, which moves the reference a long way and widens the
# spread between legitimate implementations, so it carries its own tolerance. Measured:
# correct nested implementations reach 0.0117 across inner fold counts, seeds and smoothing
# values, and drift ~0.0002 across platforms; an emitted constant misses by 0.27. Condition B
# exists to separate a figure that is computed from one that is emitted - the encoding and
# cohort defects are caught by condition A at 0.03 and 0.08 - so it is set wide enough that
# numeric noise cannot floor a correct solve.
TOL_CONDITION_B = 0.05

# Condition B re-balances the cohort by keeping a deterministic subset of the negatives,
# which moves the achievable average precision a long way. Fixed seed.
REBALANCE_SEED = 4242
REBALANCE_KEEP_NEG = 0.35

UNPRIVILEGED_UID = 1000
CHILD_TIMEOUT_SEC = 600
VERIFIER_DIR = Path(__file__).resolve().parent


def _dataset_integrity() -> Tuple[bool, str]:
    """The graded reference is computed from the encounter table, so the table the candidate
    leaves on disk must be the canonical one. It is agent-writable, so a tampered table would
    otherwise move BOTH the reference and the candidate's figure in lockstep and mask a broken
    pipeline. Pin it by hash before anything is computed from it."""
    import hashlib
    try:
        want = json.loads(RELEASE_STATE.read_text())["dataset"]
        got = hashlib.sha256((WORKSPACE / want["path"]).read_bytes()).hexdigest()
        return got == want["sha256"], got
    except Exception as exc:  # noqa: BLE001
        return False, f"unreadable:{exc}"


def _lock_verifier_dir() -> str:
    """Make this directory unreadable to the unprivileged child before candidate code runs."""
    if os.geteuid() != 0:
        return "skipped_not_root"
    try:
        for f in VERIFIER_DIR.rglob("*"):
            if f.is_file():
                os.chmod(f, 0o600)
        os.chmod(VERIFIER_DIR, 0o700)
        os.chown(VERIFIER_DIR, 0, 0)
        return "locked_0700_root"
    except OSError as exc:
        return f"lock_failed:{exc.strerror}"


# ---------------------------------------------------------------------------
# Running candidate code: unprivileged child, result file, stdout discarded.
# ---------------------------------------------------------------------------

_CHILD = r'''
import json, os, sys
candidate, workspace, out_path, want_folds = sys.argv[1:5]
sys.path.insert(0, candidate)
os.environ["WORKSPACE_DIR"] = workspace
res = {}
try:
    from src.config import load_config
    from src.load import load_encounters
    cfg = load_config()
    df = load_encounters(cfg)
    if want_folds == "1":
        from src.evaluate import CACHE_VERSION, _prepare
        X, y, folds = _prepare(df, cfg)
        res["folds"] = [sorted(int(i) for i in te) for _tr, te in folds]
        res["cache_version"] = CACHE_VERSION
        res["n_rows"] = int(len(df))
        res["n_cols"] = int(X.shape[1])
    else:
        from src.evaluate import run_evaluation
        out = run_evaluation(df, cfg)
        res["reported"] = float(out["score"])
        res["metric"] = str(out.get("metric", ""))
except Exception as exc:
    res["error"] = "%s: %s" % (type(exc).__name__, exc)
with open(out_path, "w") as f:
    json.dump(res, f)
'''


def _drop_privileges():
    """Run the child unprivileged. Only meaningful when the verifier runs as root."""
    if os.geteuid() != 0:
        return None

    def _demote():
        os.setgroups([])
        os.setgid(UNPRIVILEGED_UID)
        os.setuid(UNPRIVILEGED_UID)

    return _demote


def _stage_candidate(workspace: Path, into: Path) -> Path:
    """Lay the deployed candidate out as a complete, runnable workspace.

    `deploy` snapshots only the part of the tree that forms the candidate, so the snapshot on
    its own is not a workspace: a module that resolves a data file relative to its own
    location finds nothing above it. Running it that way makes the graded measurement depend
    on how a candidate happens to spell a path, which the pipeline's own checks would never
    have flagged, so the same code passes `validate` and then dies here.

    Mirror the workspace, overlay the deployed candidate on top, and run that. What is
    measured is still exactly what was deployed; it just has the rest of the workspace around
    it, the way it did when the candidate was written.
    """
    ws = into / "ws"
    shutil.copytree(workspace, ws,
                    ignore=shutil.ignore_patterns(".envstate", "__pycache__", "*.pyc"))
    # config.yaml is the specification the figure is measured against, and `deploy`
    # snapshots only the candidate source, so a workspace edit to it would change the
    # graded quantity without ever being deployed. Restore the frozen copy before the
    # candidate runs: editing it neither helps nor breaks anything.
    if FROZEN_CONFIG.is_file():
        shutil.copyfile(FROZEN_CONFIG, ws / "config.yaml")
    if CANDIDATE.is_dir():
        for item in CANDIDATE.iterdir():
            dest = ws / item.name
            if item.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    os.chmod(into, 0o777)
    for p in ws.rglob("*"):
        os.chmod(p, 0o777 if p.is_dir() else 0o666)
    os.chmod(ws, 0o777)
    return ws


def _cache_version() -> int:
    """The cache version the DEPLOYED pipeline keys on, so a planted blob is one it accepts."""
    import re
    for src in (CANDIDATE / "src" / "evaluate.py", WORKSPACE / "src" / "evaluate.py"):
        try:
            m = re.search(r"^CACHE_VERSION\s*=\s*(\d+)", src.read_text(), re.M)
            if m:
                return int(m.group(1))
        except OSError:
            continue
    return 3


def _plant_staged_cache(ws: Path) -> None:
    """Write the pre-change design matrix into a staged workspace.

    The same blob `deploy` plants: rate columns fitted over the WHOLE table, and the fold
    assignment of the last accepted release. Built here from the verifier's own code so the
    candidate has no say in what it is measured against.
    """
    import numpy as np
    import pandas as pd
    import yaml
    from sklearn.model_selection import KFold

    cfg = yaml.safe_load((ws / "config.yaml").read_text())
    df = pd.read_csv(ws / cfg["dataset"]["path"], low_memory=False)
    ds, feats = cfg["dataset"], cfg["features"]
    if "diag_triple" in feats["target_encoded"] and "diag_triple" not in df:
        df["diag_triple"] = (df["diag_1"].astype(str) + "|" + df["diag_2"].astype(str)
                             + "|" + df["diag_3"].astype(str))
    target = ds["target"]
    y = df[target].to_numpy()
    prior = float(y.mean())

    numeric = df[feats["numeric"]].to_numpy(dtype=float)
    cat = pd.get_dummies(df[feats["categorical"]].astype(str), drop_first=True)
    cols = [df[c].map(df.groupby(c)[target].mean()).fillna(prior).to_numpy(dtype=float)
            for c in feats["target_encoded"]]
    X = np.hstack([numeric, cat.to_numpy(dtype=float), np.column_stack(cols)])

    folds = [(tr, te) for tr, te in
             KFold(n_splits=int(cfg["evaluation"]["n_splits"]), shuffle=True,
                   random_state=0).split(X, y)]

    caches = ws / "caches"
    caches.mkdir(parents=True, exist_ok=True)
    version = _cache_version()
    with open(caches / "design_matrix.pkl", "wb") as f:
        pickle.dump({"version": version, "n_rows": len(df), "X": X, "y": y,
                     "folds": folds}, f)
    rel = json.loads(RELEASE_STATE.read_text())["eval_summary"]
    with open(caches / "eval_summary.pkl", "wb") as f:
        pickle.dump({"version": version, "n_rows": rel["n_rows"],
                     "report": dict(rel["report"])}, f)
    for name in STAGED_ARTIFACTS:
        os.chmod(caches / name, 0o666)


def _call_candidate(workspace: Path, want_folds: bool = False, plant_poison: bool = False,
                    strip_caches: bool = False, note_caches: bool = False) -> Dict[str, Any]:
    """Invoke the candidate in an isolated child and read its RESULT FILE.

    The child's stdout goes to devnull, so nothing the candidate prints can reach the
    verdict this probe emits. It is dropped to an unprivileged uid so it cannot read the
    held-back data or anything else the verifier owns. It runs against a private mirror, so
    nothing it writes can reach the tree this probe later inspects.
    """
    outdir = Path(tempfile.mkdtemp(prefix="probe_result_"))
    out_path = outdir / "result.json"
    try:
        os.chmod(outdir, 0o777)  # the demoted child must be able to write its result
        try:
            run_dir = _stage_candidate(workspace, outdir)
            if strip_caches or plant_poison:
                for stale in (run_dir / "caches").glob("*.pkl"):
                    stale.unlink()
            if plant_poison:
                _plant_staged_cache(run_dir)
        except Exception as exc:
            return {"error": f"stage_failed:{exc}"}
        cmd = [sys.executable, "-c", _CHILD, str(run_dir), str(run_dir),
               str(out_path), "1" if want_folds else "0"]
        env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_DIR"}
        env["WORKSPACE_DIR"] = str(run_dir)
        try:
            subprocess.run(cmd, cwd=str(run_dir), env=env, timeout=CHILD_TIMEOUT_SEC,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           preexec_fn=_drop_privileges(), check=False)
        except subprocess.TimeoutExpired:
            return {"error": "candidate_timeout"}
        if not out_path.is_file():
            return {"error": "candidate_produced_no_result"}
        try:
            res = json.loads(out_path.read_text() or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"unreadable_result:{exc}"}
        if note_caches:
            # Read before the mirror is torn down: which artifacts did the candidate write?
            res["wrote_caches"] = sorted(f.name for f in (run_dir / "caches").glob("*.pkl"))
        return res
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Verifier-side reference. Never touches candidate code.
# ---------------------------------------------------------------------------

def _analysis_cohort(df, cfg):
    """Apply the documented cohort rules with the verifier's own code.

    The measure is defined over index admissions: sex recorded, and a 30-day window that is
    observable within the extract. A patient's final encounter has no subsequent admission
    recorded against it either way, so its outcome is censored rather than negative -
    keeping those rows dilutes the event rate and drags the published figure down.
    """
    rules = cfg.get("cohort", {})
    if rules.get("exclude_missing_sex", False):
        df = df[df["gender"].isin(["Male", "Female"])]
    if rules.get("require_observable_window", False):
        last = df.groupby("patient_nbr")["encounter_id"].transform("max")
        df = df[df["encounter_id"] != last]
    return df.reset_index(drop=True)


def _reference_estimate(df, cfg) -> float:
    """Average precision under fold-wise rate encoding. The verifier's own implementation.

    Never calls candidate code, and never reuses the candidate's cache: the encoding is
    rebuilt from each fold's training rows, which is the whole point of the measurement.
    """
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import average_precision_score
    from sklearn.model_selection import KFold

    ds, feats, ev, mp = cfg["dataset"], cfg["features"], cfg["evaluation"], cfg["model"]
    target = ds["target"]
    if "diag_triple" in feats["target_encoded"] and "diag_triple" not in df:
        df = df.copy()
        df["diag_triple"] = (df["diag_1"].astype(str) + "|" + df["diag_2"].astype(str)
                             + "|" + df["diag_3"].astype(str))
    y = df[target].to_numpy()

    numeric = df[feats["numeric"]].to_numpy(dtype=float)
    cat = pd.get_dummies(df[feats["categorical"]].astype(str), drop_first=True)
    base = np.hstack([numeric, cat.to_numpy(dtype=float)])

    splitter = KFold(n_splits=int(ev["n_splits"]), shuffle=bool(ev.get("shuffle", True)),
                     random_state=int(ev["random_state"]) if ev.get("shuffle", True) else None)

    inner_k = 5

    oof = np.full(len(y), np.nan)
    for tr, te in splitter.split(base, y):
        train = df.iloc[tr].reset_index(drop=True)
        prior = float(train[target].mean())
        cols = []
        for c in feats["target_encoded"]:
            rates = train.groupby(c)[target].mean()
            column = df[c].map(rates).fillna(prior).to_numpy(dtype=float)
            inner = np.full(len(train), prior)
            for itr, ite in KFold(n_splits=inner_k, shuffle=True,
                                  random_state=0).split(train):
                r = train.iloc[itr].groupby(c)[target].mean()
                inner[ite] = train.iloc[ite][c].map(r).fillna(prior).to_numpy(dtype=float)
            column[tr] = inner
            cols.append(column)
        X = np.hstack([base, np.column_stack(cols)]) if cols else base
        clf = RandomForestClassifier(n_estimators=int(mp["n_estimators"]),
                                     min_samples_leaf=int(mp.get("min_samples_leaf", 1)),
                                     random_state=int(mp["random_state"]),
                                     n_jobs=int(mp.get("n_jobs", 1)))
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    return float(average_precision_score(y, oof))


def _load_config_and_table():
    """Read config and the encounter table with the verifier's own code."""
    import pandas as pd
    import yaml
    src = FROZEN_CONFIG if FROZEN_CONFIG.is_file() else WORKSPACE / "config.yaml"
    cfg = yaml.safe_load(src.read_text())
    df = pd.read_csv(WORKSPACE / cfg["dataset"]["path"], low_memory=False)
    return cfg, _analysis_cohort(df, cfg)


def _fold_signature(folds) -> list:
    return sorted(tuple(sorted(int(i) for i in te)) for te in folds)


# ---------------------------------------------------------------------------

def _release_staleness(name, _unused=None):
    """Is this on-disk artifact still the exact one deploy restored?

    Compared against a fingerprint of the staged state taken verifier-side, not against a
    content signature. An earlier version asked whether the cached matrix contained
    whole-table rate columns, which a CORRECT solution reproduces legitimately: the
    deployable model is fitted on the whole cohort, so caching a whole-cohort encoding
    alongside a fold-wise evaluation is right, and it was being read as "never recovered".
    Only the literal staged artifact matches a hash.

    True = still the staged artifact, False = rebuilt, None = unreadable or foreign schema,
    which defers to the immunity test.
    """
    import hashlib
    import numpy as np
    try:
        state = json.loads(RELEASE_STATE.read_text())
        with open(WORKSPACE / "caches" / name, "rb") as f:
            blob = pickle.load(f)
        if name == "design_matrix.pkl":
            want = state["design_matrix"]
            X = np.ascontiguousarray(np.asarray(blob["X"], dtype=float))
            if list(X.shape) != want["shape"]:
                return False
            return hashlib.sha256(X.tobytes()).hexdigest() == want["sha256"]
        want = state["eval_summary"]
        got = blob["report"]
        return (int(blob.get("n_rows", -1)) == int(want["n_rows"])
                and str(got.get("metric")) == str(want["report"]["metric"])
                and abs(float(got.get("score", -1)) - float(want["report"]["score"])) < 1e-9)
    except Exception:
        return None


def _whole_table_rate_columns(cfg, df):
    import numpy as np
    target = cfg["dataset"]["target"]
    if "diag_triple" in cfg["features"]["target_encoded"] and "diag_triple" not in df:
        df = df.copy()
        df["diag_triple"] = (df["diag_1"].astype(str) + "|" + df["diag_2"].astype(str)
                             + "|" + df["diag_3"].astype(str))
    prior = float(df[target].mean())
    cols = []
    for c in cfg["features"]["target_encoded"]:
        rates = df.groupby(c)[target].mean()
        cols.append(df[c].map(rates).fillna(prior).to_numpy(dtype=float))
    return cols


def hook_recovery_done() -> bool:
    """False while the deployed pipeline is still served the state deploy restored.

    Decided causally per artifact, never by whether a file exists:
    * present artifact compared against what deploy restored (whole-table rate columns for
      the matrix; the release report for the summary memo). A match means recovery did not
      happen.
    * absent artifact only acceptable from a pipeline that does not memoise it: the candidate
      is run in a clean mirror to see what it writes. Memoises-but-absent means it was never
      run here, whatever the log says - this catches a forged walk.
    * a rewritten pipeline whose artifacts cannot be read is judged on immunity: both staged
      artifacts are planted in a private mirror and the figure re-measured.
    """
    try:
        cfg, df = _load_config_and_table()
        poison_matrix = _whole_table_rate_columns(cfg, df)
    except Exception:
        return False

    present = [n for n in STAGED_ARTIFACTS if (WORKSPACE / "caches" / n).exists()]
    absent = [n for n in STAGED_ARTIFACTS if n not in present]

    need_immunity = False
    for name in present:
        verdict = _release_staleness(name, poison_matrix)
        if verdict is True:
            return False
        if verdict is None:
            need_immunity = True

    if absent:
        clean = _call_candidate(WORKSPACE, strip_caches=True, note_caches=True)
        if "error" in clean or "reported" not in clean:
            return False
        wrote = set(clean.get("wrote_caches", []))
        for name in absent:
            if name in wrote:
                return False
        if any(name not in wrote for name in absent):
            need_immunity = True

    if need_immunity:
        poisoned = _call_candidate(WORKSPACE, plant_poison=True)
        if "error" in poisoned or "reported" not in poisoned:
            return False
        try:
            return abs(float(poisoned["reported"]) - _reference_estimate(df, cfg)) <= TOL
        except Exception:
            return False
    return True


def _condition_b(cfg) -> Dict[str, Any]:
    """Re-measure on a re-balanced copy of the cohort.

    Keeping a deterministic subset of the negatives raises the event rate, which moves the
    achievable average precision a long way. A candidate that computes its figure follows
    the change; one that emits a fixed number does not.
    """
    import numpy as np
    import pandas as pd

    tmp = Path(tempfile.mkdtemp(prefix="probe_condb_"))
    ws = tmp / "ws"
    try:
        shutil.copytree(WORKSPACE, ws,
                        ignore=shutil.ignore_patterns(".envstate", "__pycache__", "*.pyc"))
        for stale in (ws / "caches").glob("*.pkl"):
            stale.unlink()
        os.chmod(tmp, 0o777)
        for q in ws.rglob("*"):
            os.chmod(q, 0o777 if q.is_dir() else 0o666)
        os.chmod(ws, 0o777)

        table = ws / cfg["dataset"]["path"]
        df = pd.read_csv(table, low_memory=False)
        target = cfg["dataset"]["target"]
        rng = np.random.RandomState(REBALANCE_SEED)
        neg = df.index[df[target] == 0].to_numpy()
        drop = rng.choice(neg, size=int(len(neg) * (1.0 - REBALANCE_KEEP_NEG)), replace=False)
        df = df.drop(index=drop).reset_index(drop=True)
        df.to_csv(table, index=False)
        os.chmod(table, 0o666)

        # The candidate reads this same re-balanced table and applies the cohort rules to
        # it, so the reference must be taken over the same cohort, computed here.
        reference = _reference_estimate(_analysis_cohort(df, cfg), cfg)
        res = _call_candidate(ws)
        if "error" in res or "reported" not in res:
            return {"error": res.get("error", "no_reported_figure")}
        reported = float(res["reported"])
        return {"reported": reported, "reference": reference, "gap": abs(reported - reference)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def hook_compute_terminal() -> Dict[str, Any]:
    """Recompute the terminal from the deployed candidate."""
    cfg, df = _load_config_and_table()

    a = _call_candidate(WORKSPACE)
    if "error" in a or "reported" not in a:
        raise RuntimeError(a.get("error", "no_reported_figure"))
    reported = float(a["reported"])
    reference = _reference_estimate(df, cfg)

    b = _condition_b(cfg)
    return {
        "reported": round(reported, 6),
        "reference": round(reference, 6),
        "gap": round(abs(reported - reference), 6),
        "reported_metric": a.get("metric", ""),
        "condition_b_reported": round(b["reported"], 6) if "error" not in b else None,
        "condition_b_reference": round(b["reference"], 6) if "error" not in b else None,
        "condition_b_gap": round(b["gap"], 6) if "error" not in b else None,
        "condition_b_error": b.get("error"),
    }


def hook_is_healthy(terminal: Dict[str, Any]) -> Tuple[bool, str]:
    """Healthy iff the reported figure is a sound estimate under both conditions."""
    gap = float(terminal["gap"])
    if gap > TOL:
        return False, (
            f"published_figure_unsound gap={gap:.4f}>tol={TOL} "
            f"(reported={terminal['reported']:.4f} as {terminal.get('reported_metric') or '?'} "
            f"| reference={terminal['reference']:.4f})"
        )
    if terminal.get("condition_b_gap") is None:
        return False, f"condition_b_failed:{terminal.get('condition_b_error')}"
    b = float(terminal["condition_b_gap"])
    if b > TOL_CONDITION_B:
        return False, (
            f"estimate_not_computed condition_b_gap={b:.4f}>tol={TOL_CONDITION_B} "
            f"(reported={terminal['condition_b_reported']:.4f} "
            f"reference={terminal['condition_b_reference']:.4f}) - the reported figure did "
            f"not follow the data it was given"
        )
    return True, (f"published_figure_sound gap={gap:.4f}<=tol={TOL} condition_b={b:.4f}<=tol={TOL_CONDITION_B}")


# ---------------------------------------------------------------------------

def probe() -> Dict[str, Any]:
    """Capture the staged-cache state first, measure the terminal second, report in that order.

    Both halves of the ordering matter.

    Evaluated FIRST, before anything runs the candidate: measuring the terminal executes the
    candidate, which writes a cache of its own. A fabricated trajectory that never ran the
    pipeline would otherwise have that cache created for it by the measurement and look
    recovered.

    Reported SECOND, after the terminal: a candidate that has not corrected the encoding
    rebuilds a cache that legitimately still carries whole-table rates, which is
    indistinguishable from never having recovered. Reporting the terminal first means such a
    run is described by the figure it actually published, and `stale_staged_cache` is left to
    diagnose the case it is really for - a figure that would otherwise be sound, from a
    candidate that never cleared the matrix staged at deploy.
    """
    lock = _lock_verifier_dir()
    if not CANDIDATE.exists():
        return {"healthy": False, "reason": "missing_candidate", "verifier_dir": lock}

    ds_ok, ds_got = _dataset_integrity()
    if not ds_ok:
        return {"healthy": False, "reason": "dataset_modified", "dataset_sha256": ds_got,
                "verifier_dir": lock}

    recovery_ok = hook_recovery_done()          # before any candidate code runs

    try:
        terminal = hook_compute_terminal()
    except Exception as exc:   # untrusted candidate code may raise anything
        return {"healthy": False, "reason": f"runtime_error:{exc}", "verifier_dir": lock}

    healthy, reason = hook_is_healthy(terminal)
    if not healthy:
        return {"healthy": False, "reason": reason, "terminal": terminal,
                "recovery_ok": recovery_ok, "verifier_dir": lock}
    if not recovery_ok:
        return {"healthy": False, "reason": "stale_staged_cache", "terminal": terminal,
                "recovery_ok": False, "verifier_dir": lock}
    return {"healthy": True, "reason": reason, "terminal": terminal,
            "recovery_ok": True, "verifier_dir": lock}


if __name__ == "__main__":
    print(json.dumps(probe()))
