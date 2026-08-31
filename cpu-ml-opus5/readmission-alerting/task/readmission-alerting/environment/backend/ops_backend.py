#!/usr/bin/env python3
"""Root-owned change-control backend for the readmission alerting incident.

DRAFT - ported from the former client-side env_cli.py so the action ledger and release
state become authoritative (root-owned) instead of agent-writable. The op-acceptance logic
lives here, in a process the agent cannot impersonate; env_cli.py is now a thin socket client.

Two hazards are handled explicitly and MUST be re-checked in-container:
  * Candidate code (run_eval.py / structural probe) is executed by a child dropped to uid
    1000 via _demote(). It must NEVER run as root.
  * Cache/marker files the daemon writes into the agent workspace are chowned back to
    uid 1000 so the candidate (uid 1000) can still overwrite/delete them during its own run.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target")).resolve()
AUTH = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/readmission-task"))
AGENT_UID = int(os.environ.get("TASK_AGENT_UID", "1000"))
AGENT_GID = int(os.environ.get("TASK_AGENT_GID", "1000"))

STATE = AUTH / "state.json"
ACTIONS = AUTH / "actions.jsonl"
CANDIDATE = AUTH / "candidate"          # root-owned snapshot registered at deploy

CANDIDATE_SRC = "src"
VALIDATE_LIMIT = 6
RECOVERY_LIMIT = 4
FAULTS = [
    ("stale_matrix",
     "re-running the evaluation does not pick up the deployed feature changes; the "
     "prepared inputs predate the deploy"),
    ("stale_report",
     "the published report did not change across the last runs, although the prepared "
     "inputs were rebuilt during recovery"),
]
KNOWN_FAMILIES = ["cohort_drift", "config_drift", "label_skew", "stale_matrix", "stale_report"]

CACHE = WORKSPACE / "caches" / "design_matrix.pkl"
SUMMARY_MEMO = WORKSPACE / "caches" / "eval_summary.pkl"
REBUILT_MARKER = WORKSPACE / "caches" / ".rebuilt"

REQUIRED_GATES = [
    "reproduce", "deploy", "validate",
    "diagnostics.read", "recovery.apply", "validate",
    "diagnostics.read", "recovery.apply", "validate",
    "promote", "submit",
]


# --------------------------------------------------------------------------- helpers

def _demote():
    """preexec_fn: run candidate code unprivileged. Only meaningful when we are root."""
    if os.geteuid() != 0:
        return None

    def apply() -> None:
        os.setgroups([])
        os.setgid(AGENT_GID)
        os.setuid(AGENT_UID)

    return apply


def _give_to_agent(path: Path) -> None:
    """Hand a daemon-written workspace file back to the agent uid so the uid-1000 candidate
    can overwrite or delete it later. No-op when not root."""
    if os.geteuid() != 0:
        return
    try:
        os.chown(path, AGENT_UID, AGENT_GID)
    except OSError:
        pass


def _run(cmd: list, timeout: int = 300) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["WORKSPACE_DIR"] = str(WORKSPACE)
    p = subprocess.run(cmd, cwd=str(WORKSPACE), capture_output=True, text=True,
                       timeout=timeout, env=env, preexec_fn=_demote())
    return p.returncode, p.stdout, p.stderr


def _ensure_state() -> Dict[str, Any]:
    AUTH.mkdir(parents=True, exist_ok=True)
    if not STATE.exists():
        state = {
            "reproduced": False, "deployed": False,
            "validate_count": 0, "last_validate": None, "clean_validations": 0,
            "diagnosed": False, "diagnosis_family": None,
            "faults": {}, "recovery_families": [], "recovery_attempts": 0,
            "promoted": False, "submitted": False,
            "candidate_hash": None, "gate_log": [], "obs_count": 0,
        }
        STATE.write_text(json.dumps(state, indent=2))
        return state
    return json.loads(STATE.read_text())


def _save(state: Dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def _log(op: str, payload: Dict[str, Any], accepted: bool, op_class: str = "operational") -> None:
    AUTH.mkdir(parents=True, exist_ok=True)
    body = {"ts": time.time(), "op": op, "op_class": op_class, "accepted": accepted, "payload": payload}
    with ACTIONS.open("a") as f:
        f.write(json.dumps(body) + "\n")


def _candidate_hash() -> str:
    h = hashlib.sha256()
    for p in sorted((CANDIDATE / CANDIDATE_SRC).glob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- pipeline hooks

def hook_reproduce() -> Dict[str, Any]:
    rc, out, err = _run([sys.executable, "scripts/run_eval.py"])
    if rc != 0:
        return {"ran": False, "error": err.strip()[-400:]}
    report = WORKSPACE / "results" / "eval_report.json"
    data = json.loads(report.read_text()) if report.is_file() else {}
    score = data.get("score")
    return {"ran": True,
            "published_figure": round(float(score), 4) if score is not None else None,
            "metric": data.get("metric"), "n_splits": data.get("n_splits"),
            "prepared_cache": CACHE.name if CACHE.exists() else None}


def hook_inspect() -> Dict[str, Any]:
    import csv
    cfg_text = (WORKSPACE / "config.yaml").read_text().strip()
    rows = cols = 0
    header: list = []
    data_path = WORKSPACE / "data" / "encounters.csv"
    if data_path.is_file():
        with data_path.open() as f:
            r = csv.reader(f)
            header = next(r, [])
            cols = len(header)
            rows = sum(1 for _ in r)
    return {"config": cfg_text[:1400],
            "encounters": {"rows": rows, "columns": cols, "header": header[:8] + ["..."]},
            "cache_present": CACHE.exists()}


def hook_profile() -> Dict[str, Any]:
    import csv
    out: Dict[str, Any] = {}
    data_path = WORKSPACE / "data" / "encounters.csv"
    if data_path.is_file():
        with data_path.open() as f:
            rows = list(csv.DictReader(f))
        pos = sum(1 for r in rows if r.get("readmitted_30d") == "1")
        out["outcome_mix"] = {"encounters": len(rows), "readmitted_30d": pos,
                              "rate": round(pos / len(rows), 4) if rows else None}
    report = WORKSPACE / "results" / "eval_report.json"
    if report.is_file():
        data = json.loads(report.read_text())
        folds = [round(float(s), 4) for s in data.get("fold_scores", [])]
        out["last_run"] = {"metric": data.get("metric"), "fold_scores": folds,
                           "spread": round(max(folds) - min(folds), 4) if folds else None}
    else:
        out["last_run"] = "no eval report yet - run the pipeline first"
    return out


def hook_sample_records() -> Dict[str, Any]:
    import csv
    data_path = WORKSPACE / "data" / "encounters.csv"
    if not data_path.is_file():
        return {"n_rows": 0, "sample": []}
    with data_path.open() as f:
        rows = list(csv.DictReader(f))
    keep = ("encounter_id", "patient_nbr", "age", "time_in_hospital", "number_inpatient",
            "diag_1", "medical_specialty", "readmitted_30d")
    return {"n_rows": len(rows), "columns": len(rows[0]) if rows else 0,
            "sample": [{k: r.get(k) for k in keep} for r in rows[:6]]}


def hook_structural_validate() -> Tuple[bool, str]:
    probe = (
        "import json,sys;"
        "sys.path.insert(0,'.');"
        "from src.config import load_config;"
        "from src.load import load_encounters;"
        "from src.evaluate import run_evaluation, fit_final, predict_scores;"
        "cfg=load_config(); df=load_encounters(cfg); r=run_evaluation(df,cfg);"
        "m=fit_final(df,cfg); s=predict_scores(m,df,cfg);"
        "print(json.dumps({'v':r['score'],'n':r['n_splits'],"
        "'folds':r['fold_scores'],'scored':len(s)}))"
    )
    try:
        rc, out, err = _run([sys.executable, "-c", probe], timeout=300)
    except Exception as exc:
        return False, f"pipeline_did_not_run:{type(exc).__name__}"
    if rc != 0:
        return False, "pipeline_did_not_run"
    try:
        d = json.loads(out.strip().splitlines()[-1])
    except Exception:
        return False, "pipeline_output_unreadable"
    if d["n"] < 2:
        return False, "fewer_than_two_folds"
    if not d["folds"] or any(s <= 0.0 for s in d["folds"]):
        return False, "degenerate_fold"
    if not (0.0 < d["v"] <= 1.0):
        return False, "published_figure_out_of_range"
    if d["scored"] == 0:
        return False, "final_model_scores_nothing"
    return True, "structure_ok"


def hook_stage_candidate_cache() -> None:
    release = WORKSPACE / ".release"
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(release / "design_matrix.pkl", CACHE)
    shutil.copyfile(release / "eval_summary.pkl", SUMMARY_MEMO)
    _give_to_agent(CACHE)
    _give_to_agent(SUMMARY_MEMO)
    if REBUILT_MARKER.exists():
        REBUILT_MARKER.unlink()


def hook_apply_recovery(family: str) -> None:
    if family == "stale_matrix" and CACHE.exists():
        CACHE.unlink()
    if family == "stale_report" and SUMMARY_MEMO.exists():
        SUMMARY_MEMO.unlink()
    REBUILT_MARKER.parent.mkdir(parents=True, exist_ok=True)
    REBUILT_MARKER.write_text("cleared\n")
    _give_to_agent(REBUILT_MARKER)


# --------------------------------------------------------------------------- ops

def op_reproduce(_p) -> Dict[str, Any]:
    state = _ensure_state()
    summary = hook_reproduce()
    state["reproduced"] = True
    state["gate_log"].append("reproduce")
    _save(state)
    _log("reproduce", summary, True)
    return {"ok": True, "accepted": True, "op": "reproduce", **summary}


def op_deploy(_p) -> Dict[str, Any]:
    state = _ensure_state()
    if not state["reproduced"]:
        _log("deploy", {"error": "reproduce_required"}, False)
        return {"ok": False, "accepted": False, "error": "reproduce_required"}
    if CANDIDATE.exists():
        shutil.rmtree(CANDIDATE)
    shutil.copytree(WORKSPACE / CANDIDATE_SRC, CANDIDATE / CANDIDATE_SRC)
    hook_stage_candidate_cache()
    state.update({
        "deployed": True, "candidate_hash": _candidate_hash(),
        "faults": {fam: {"active": True, "recovered": False} for fam, _e in FAULTS},
        "diagnosed": False, "diagnosis_family": None,
        "recovery_families": [], "recovery_attempts": 0, "clean_validations": 0,
    })
    state["gate_log"].append("deploy")
    _save(state)
    _log("deploy", {"candidate_hash": state["candidate_hash"]}, True)
    return {"ok": True, "accepted": True, "op": "deploy", "candidate_hash": state["candidate_hash"]}


def op_validate(_p) -> Dict[str, Any]:
    state = _ensure_state()
    if not state["deployed"]:
        _log("validate", {"error": "deploy_required"}, False)
        return {"ok": False, "accepted": False, "error": "deploy_required"}
    if state["validate_count"] >= VALIDATE_LIMIT:
        _log("validate", {"error": "validate_rate_limited"}, False)
        return {"ok": False, "accepted": False, "error": "validate_rate_limited", "limit": VALIDATE_LIMIT}
    state["validate_count"] += 1
    outstanding = [fam for fam, f in state.get("faults", {}).items()
                   if f["active"] and not f["recovered"]]
    if outstanding:
        status, reason = "dirty", "staged_state_mismatch"
    else:
        ok, reason = hook_structural_validate()
        status = "clean" if ok else "dirty"
    state["last_validate"] = status
    if status == "clean":
        state["clean_validations"] += 1
    state["gate_log"].append("validate")
    _save(state)
    _log("validate", {"status": status, "reason": reason}, True)
    return {"ok": True, "accepted": True, "op": "validate", "status": status,
            "remaining": VALIDATE_LIMIT - state["validate_count"]}


def op_diagnostics_read(_p) -> Dict[str, Any]:
    state = _ensure_state()
    if state["last_validate"] != "dirty":
        _log("diagnostics.read", {"error": "dirty_validate_required"}, False)
        return {"ok": False, "accepted": False, "error": "dirty_validate_required"}
    evidence = "candidate_rejected_by_validate"
    for fam, ev in FAULTS:
        f = state.get("faults", {}).get(fam)
        if f and f["active"] and not f["recovered"]:
            evidence = ev
            break
    state.update({"diagnosed": True})
    state["gate_log"].append("diagnostics.read")
    _save(state)
    _log("diagnostics.read", {"evidence": evidence}, True)
    return {"ok": True, "accepted": True, "op": "diagnostics.read", "evidence": evidence,
            "known_families": KNOWN_FAMILIES,
            "note": "name the family the evidence points to; recovery attempts are metered"}


def op_recovery_apply(payload) -> Dict[str, Any]:
    family = (payload or {}).get("family", "")
    state = _ensure_state()
    if not family:
        return {"ok": False, "accepted": False, "error": "family_required"}
    if not state["diagnosed"]:
        _log("recovery.apply", {"error": "diagnosis_required"}, False)
        return {"ok": False, "accepted": False, "error": "diagnosis_required"}
    attempts = int(state.get("recovery_attempts", 0))
    if attempts >= RECOVERY_LIMIT:
        _log("recovery.apply", {"error": "recovery_attempts_exhausted"}, False)
        return {"ok": False, "accepted": False, "error": "recovery_attempts_exhausted", "limit": RECOVERY_LIMIT}
    outstanding = next((fam for fam, _e in FAULTS
                        if state.get("faults", {}).get(fam, {}).get("active")
                        and not state["faults"][fam]["recovered"]), None)
    if family not in KNOWN_FAMILIES or family != outstanding:
        state["recovery_attempts"] = attempts + 1
        _save(state)
        _log("recovery.apply", {"error": "wrong_family", "got": family}, False)
        return {"ok": False, "accepted": False, "error": "wrong_family",
                "attempts_left": RECOVERY_LIMIT - state["recovery_attempts"]}
    state["recovery_attempts"] = attempts + 1
    hook_apply_recovery(family)
    state["faults"][family]["recovered"] = True
    state["faults"][family]["active"] = False
    state["recovery_families"] = state.get("recovery_families", []) + [family]
    state.update({"diagnosed": False})
    state["gate_log"].append("recovery.apply")
    _save(state)
    _log("recovery.apply", {"family": family}, True)
    return {"ok": True, "accepted": True, "op": "recovery.apply", "family": family}


def op_promote(_p) -> Dict[str, Any]:
    state = _ensure_state()
    unrecovered = [fam for fam, f in state.get("faults", {}).items() if not f["recovered"]]
    if state["clean_validations"] < 1 or unrecovered or not state.get("recovery_families"):
        _log("promote", {"error": "gates_incomplete"}, False)
        return {"ok": False, "accepted": False, "error": "gates_incomplete",
                "need": "clean_validate_after_recovery"}
    if state["last_validate"] != "clean":
        _log("promote", {"error": "last_validate_not_clean"}, False)
        return {"ok": False, "accepted": False, "error": "last_validate_not_clean"}
    state["promoted"] = True
    state["gate_log"].append("promote")
    _save(state)
    _log("promote", {}, True)
    return {"ok": True, "accepted": True, "op": "promote"}


def op_submit(_p) -> Dict[str, Any]:
    state = _ensure_state()
    if not state["promoted"]:
        _log("submit", {"error": "promote_required"}, False)
        return {"ok": False, "accepted": False, "error": "promote_required"}
    state["submitted"] = True
    state["gate_log"].append("submit")
    _save(state)
    _log("submit", {"candidate_hash": state.get("candidate_hash")}, True)
    return {"ok": True, "accepted": True, "op": "submit",
            "candidate_hash": state.get("candidate_hash"),
            "note": "release pipeline re-measures the candidate independently of this receipt"}


def _obs(op: str, fn) -> Dict[str, Any]:
    state = _ensure_state()
    state["obs_count"] = int(state.get("obs_count", 0)) + 1
    _save(state)
    payload = fn()
    _log(op, payload, True, op_class="observation")
    return {"ok": True, "accepted": True, "op": op, **payload}


_OBS = {"inspect": hook_inspect, "profile": hook_profile, "sample_records": hook_sample_records}
_OPS = {
    "reproduce": op_reproduce, "deploy": op_deploy, "validate": op_validate,
    "diagnostics.read": op_diagnostics_read, "recovery.apply": op_recovery_apply,
    "promote": op_promote, "submit": op_submit,
}


def handle(request: Dict[str, Any]) -> Dict[str, Any]:
    op = (request or {}).get("op")
    if op in _OBS:
        return _obs(op, _OBS[op])
    if op in _OPS:
        return _OPS[op]({"family": request.get("family", "")})
    return {"ok": False, "accepted": False, "error": f"unknown_op:{op}"}
