#!/usr/bin/env python3
"""Privileged, immutable operation broker for the as-of incident."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STATE_DIR_DEFAULT = "/var/lib/asof-task"


def _pinned_dir(pinned: Path) -> Path:
    """Return an authoritative directory that cannot be redirected in the served path.

    The broker runs as root (see ``main``); the verifier runs as root too. Whenever the
    process is privileged the authoritative location is the hard-coded default and no
    environment variable can move it, so neither the agent nor a forged env can point the
    root-of-trust at a look-alike. A relocation is honoured ONLY when running unprivileged,
    which happens exclusively during off-host reproduction and never in the graded path.
    """
    if os.geteuid() == 0:
        return pinned
    override = os.environ.get("ASOF_OFFLINE_STATE_DIR")
    return Path(override) if override else pinned


ROOT = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
AUTH = _pinned_dir(Path(STATE_DIR_DEFAULT))
STATE = AUTH / "state.json"
ACTIONS = AUTH / "actions.jsonl"
CANDIDATE = AUTH / "candidate"
LOCK = AUTH / "lock"
DATA = ROOT / "data/transactions.parquet"
LIMIT = 6
SAMPLE_COUNT = 13
FAMILY = "stale_cache"
INSTANCE_SEED = int(os.environ.get("TASK_INSTANCE_SEED", "41729"))
ASSET_DIR = Path(__file__).resolve().parent
VALIDATION_TX = ASSET_DIR / "validation_transactions.parquet"
VALIDATION_PROBES = ASSET_DIR / "validation_probes.parquet"
VALIDATION_ORACLE = ASSET_DIR / "validation_oracle.json"
WORKER = ASSET_DIR / "candidate_worker.py"

EXAMPLES = [
    {
        "case": "late_finance_document",
        "transactions": [
            ["810", "A", 2, "2020-03-10 10:00", 1.00, False],
            ["C910", "A", -1, "2020-04-02 10:00", 1.00, True],
        ],
        "probes": ["2020-04-01 00:00", "2020-04-04 00:00"],
        "expected": [
            {"n_lines": 1, "n_sku": 1, "n_credit": 0, "spend_cents": 200, "days_since_last": 21},
            {"n_lines": 2, "n_sku": 1, "n_credit": 1, "spend_cents": 100, "days_since_last": 24},
        ],
    },
    {
        "case": "settlement_boundary",
        "transactions": [["820", "B", 3, "2020-03-31 00:00", 1.00, False]],
        "probes": ["2020-04-01 00:00", "2020-04-01 12:00", "2020-04-02 00:00"],
        "expected": [
            {"n_lines": 0, "n_sku": 0, "n_credit": 0, "spend_cents": 0, "days_since_last": -1},
            {"n_lines": 1, "n_sku": 1, "n_credit": 0, "spend_cents": 300, "days_since_last": 1},
            {"n_lines": 1, "n_sku": 1, "n_credit": 0, "spend_cents": 300, "days_since_last": 2},
        ],
    },
    {
        "case": "strict_prior_match",
        "transactions": [
            ["830", "C", 2, "2020-01-02 09:00", 1.00, False],
            ["C930", "C", -1, "2020-01-02 09:00", 1.00, True],
        ],
        "probes": ["2020-01-05 00:00"],
        "expected": [{"n_lines": 1, "n_sku": 1, "n_credit": 0, "spend_cents": 200, "days_since_last": 2}],
    },
    {
        "case": "most_recent_prior_across_window",
        "transactions": [
            ["840", "D", 2, "2020-01-01 09:00", 1.00, False],
            ["841", "D", 3, "2020-03-20 09:00", 1.00, False],
            ["C940", "D", -1, "2020-04-02 09:00", 1.00, True],
        ],
        "probes": ["2020-04-04 00:00", "2020-06-25 00:00"],
        "expected": [
            {"n_lines": 2, "n_sku": 1, "n_credit": 1, "spend_cents": 200, "days_since_last": 14},
            {"n_lines": 0, "n_sku": 0, "n_credit": 0, "spend_cents": 0, "days_since_last": -1},
        ],
    },
    {
        "case": "unmatched_credit",
        "transactions": [["C950", "E", -4, "2020-02-03 12:00", 1.00, True]],
        "probes": ["2020-02-06 00:00"],
        "expected": [{"n_lines": 0, "n_sku": 0, "n_credit": 0, "spend_cents": 0, "days_since_last": -1}],
    },
    {
        "case": "duplicate_source_lines",
        "transactions": [
            ["860", "F", 1, "2020-02-01 09:00", 1.00, False],
            ["860", "F", 1, "2020-02-01 09:00", 1.00, False],
            ["C960", "F", -1, "2020-02-04 09:00", 1.00, True],
        ],
        "probes": ["2020-02-07 00:00"],
        "expected": [{"n_lines": 3, "n_sku": 1, "n_credit": 1, "spend_cents": 100, "days_since_last": 5}],
    },
    {
        "case": "insufficient_recent_capacity",
        "transactions": [
            ["870", "G", 5, "2020-02-01 09:00", 1.00, False],
            ["871", "G", 1, "2020-04-01 09:00", 1.00, False],
            ["C970", "G", -3, "2020-04-03 09:00", 1.00, True],
        ],
        "probes": ["2020-04-06 00:00", "2020-06-15 00:00"],
        "expected": [
            {"n_lines": 3, "n_sku": 1, "n_credit": 1, "spend_cents": 300, "days_since_last": 4},
            {"n_lines": 1, "n_sku": 1, "n_credit": 0, "spend_cents": 100, "days_since_last": 74},
        ],
    },
    {
        "case": "unit_price_identity",
        "transactions": [
            ["880", "H", 5, "2020-03-01 09:00", 1.00, False],
            ["881", "H", 5, "2020-03-20 09:00", 2.00, False],
            ["C980", "H", -2, "2020-04-02 09:00", 1.00, True],
        ],
        "probes": ["2020-04-05 00:00", "2020-06-01 00:00"],
        "expected": [
            {"n_lines": 3, "n_sku": 1, "n_credit": 1, "spend_cents": 1300, "days_since_last": 15},
            {"n_lines": 1, "n_sku": 1, "n_credit": 0, "spend_cents": 1000, "days_since_last": 72},
        ],
    },
    {
        "case": "capacity_is_consumed",
        "transactions": [
            ["890", "I", 3, "2020-03-01 09:00", 1.00, False],
            ["C990", "I", -2, "2020-03-10 09:00", 1.00, True],
            ["C991", "I", -2, "2020-03-20 09:00", 1.00, True],
        ],
        "probes": ["2020-04-01 00:00"],
        "expected": [
            {"n_lines": 2, "n_sku": 1, "n_credit": 1, "spend_cents": 100, "days_since_last": 30},
        ],
    },
    {
        "case": "credit_is_indivisible",
        "transactions": [
            ["900", "J", 2, "2020-03-01 09:00", 1.00, False],
            ["901", "J", 2, "2020-03-10 09:00", 1.00, False],
            ["C1000", "J", -3, "2020-03-20 09:00", 1.00, True],
        ],
        "probes": ["2020-04-01 00:00"],
        "expected": [
            {"n_lines": 2, "n_sku": 1, "n_credit": 0, "spend_cents": 400, "days_since_last": 21},
        ],
    },
    {
        "case": "equal_time_order_tie_break",
        "transactions": [
            ["911", "K", 3, "2020-03-01 09:00", 1.00, False],
            ["910", "K", 5, "2020-03-01 09:00", 1.00, False],
            ["C1010", "K", -3, "2020-03-10 09:00", 1.00, True],
            ["C1011", "K", -4, "2020-03-20 09:00", 1.00, True],
        ],
        "probes": ["2020-04-01 00:00"],
        "expected": [
            {"n_lines": 3, "n_sku": 1, "n_credit": 1, "spend_cents": 500, "days_since_last": 30},
        ],
    },
    {
        "case": "equal_time_credit_order",
        "transactions": [
            ["920", "L", 3, "2020-03-01 09:00", 1.00, False],
            ["C1021", "L", -2, "2020-03-10 09:00", 1.00, True],
            ["C1020", "L", -3, "2020-03-10 09:00", 1.00, True],
        ],
        "probes": ["2020-04-01 00:00"],
        "expected": [
            {"n_lines": 2, "n_sku": 1, "n_credit": 1, "spend_cents": 100, "days_since_last": 30},
        ],
    },
    {
        "case": "price_normalized_to_cents",
        "transactions": [
            ["930", "M", 4, "2020-03-01 09:00", 1.004, False],
            ["C1030", "M", -2, "2020-03-10 09:00", 1.00, True],
        ],
        "probes": ["2020-04-01 00:00"],
        "expected": [
            {"n_lines": 2, "n_sku": 1, "n_credit": 1, "spend_cents": 202, "days_since_last": 30},
        ],
    },
]


def initial():
    return {"investigation": [], "sample_cursor": 0, "reproduced": False,
            "deployed": False, "validate_count": 0, "last_validate": None,
            "diagnosed": False, "recovered": False, "promoted": False,
            "submitted": False, "candidate_hash": None, "generation": 0}


def load():
    AUTH.mkdir(mode=0o700, parents=True, exist_ok=True)
    return json.loads(STATE.read_text()) if STATE.exists() else initial()


def save(s):
    tmp = AUTH / "state.tmp"
    tmp.write_text(json.dumps(s, indent=2)); os.chmod(tmp, 0o600); tmp.replace(STATE)


def emit(s, op, payload, accepted=True, cls="operational"):
    row = {"seq": sum(1 for _ in ACTIONS.open()) + 1 if ACTIONS.exists() else 1,
           "ts": time.time(), "op": op, "op_class": cls,
           "accepted": accepted, "payload": payload,
           "generation": s.get("generation", 0)}
    with ACTIONS.open("a") as f: f.write(json.dumps(row) + "\n")
    os.chmod(ACTIONS, 0o600)
    print(json.dumps({"ok": accepted, "op": op, **payload}, indent=2))
    save(s)
    return 0 if accepted else 1


def tx():
    import pandas as pd
    d = pd.read_parquet(DATA); d["InvoiceDate"] = pd.to_datetime(d["InvoiceDate"]); return d


def candidate_hash():
    h = hashlib.sha256()
    for p in sorted((CANDIDATE / "src").glob("*.py")): h.update(p.read_bytes())
    return h.hexdigest()[:16]


def snapshot_candidate():
    """Copy flat Python sources without ever following an agent-controlled symlink."""
    source_dir = os.open(ROOT / "src", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        names = sorted(name for name in os.listdir(source_dir) if name.endswith(".py"))
        if not names or len(names) > 32:
            raise ValueError("invalid candidate source set")
        CANDIDATE.mkdir(mode=0o755, parents=True)
        target = CANDIDATE / "src"; target.mkdir(mode=0o755)
        for name in names:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_dir)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                    raise ValueError(f"invalid candidate file:{name}")
                data = b""
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk: break
                    data += chunk
            finally:
                os.close(fd)
            out_fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                view = memoryview(data)
                while view: view = view[os.write(out_fd, view):]
            finally: os.close(out_fd)
    finally:
        os.close(source_dir)


def validation_correct():
    expected = json.loads(VALIDATION_ORACLE.read_text())
    with tempfile.TemporaryDirectory(dir=AUTH) as tmp_name:
        tmp = Path(tmp_name)
        os.chmod(tmp, 0o733)
        staged = {}
        for source, name in [(WORKER, "worker.py"), (VALIDATION_TX, "tx.parquet"),
                             (VALIDATION_PROBES, "probes.parquet")]:
            target = tmp / name
            shutil.copyfile(source, target)
            os.chmod(target, 0o444)
            staged[name] = target
        output = tmp / "features.parquet"

        def drop_privileges():
            if os.geteuid() == 0:
                os.setgroups([]); os.setgid(1000); os.setuid(1000)

        proc = subprocess.run(
            [sys.executable, "-I", str(staged["worker.py"]), "--candidate", str(CANDIDATE),
             "--transactions", str(staged["tx.parquet"]), "--probes", str(staged["probes.parquet"]),
             "--output", str(output)], cwd=tmp, capture_output=True, text=True, timeout=900,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0",
                 "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
            preexec_fn=drop_privileges,
        )
        if proc.returncode != 0 or not output.exists():
            return False
        import pandas as pd
        frame = pd.read_parquet(output)
    cols = ["customer_id", "as_of", *expected["graded_columns"]]
    if len(frame) != expected["n_rows"] or any(c not in frame for c in cols):
        return False
    frame = frame.loc[:, cols].copy()
    frame["as_of"] = frame["as_of"].astype("datetime64[ns]").astype("int64")
    for col in ["customer_id", *expected["graded_columns"]]:
        frame[col] = frame[col].astype("int64")
    frame = frame.sort_values(["as_of", "customer_id"], kind="stable")
    canonical = "\n".join("\t".join(str(v) for v in row)
                           for row in frame.itertuples(index=False, name=None))
    digest = hashlib.sha256((expected["salt"] + "\n" + canonical).encode()).hexdigest()
    return digest == expected["terminal_sha256"]


def handle(op, args, s):
    history = s["investigation"]
    if op == "inspect":
        if "inspect" in history: return emit(s, op, {"error":"already_completed"}, False, "observation")
        d=tx(); history.append(op)
        return emit(s,op,{"columns":list(d.columns),"rows":len(d),
            "date_range":[str(d.InvoiceDate.min()),str(d.InvoiceDate.max())],
            "next":"profile"},True,"observation")
    if op == "profile":
        if history != ["inspect"]: return emit(s,op,{"error":"inspect_required"},False,"observation")
        d=tx(); c=d[d.is_credit]; history.append(op)
        return emit(s,op,{"credit_lines":len(c),"credit_share":round(float(d.is_credit.mean()),5),
            "finding":"credit documents are raised after the commercial event; inspect paired records",
            "next":"sample_records --cursor 0"},True,"observation")
    if op == "sample_records":
        cur=args.cursor
        if history[:2] != ["inspect","profile"] or cur != s["sample_cursor"] or cur >= SAMPLE_COUNT:
            return emit(s,op,{"error":"next_cursor_required","expected":s["sample_cursor"]},False,"observation")
        s["sample_cursor"] += 1; history.append(f"sample:{cur}")
        case = EXAMPLES[(cur + INSTANCE_SEED) % len(EXAMPLES)]
        payload={"cursor":cur,
                 "transaction_columns":["Invoice","StockCode","Quantity","InvoiceDate","Price","is_credit"],
                 "note":"worked examples use one customer and prices are GBP per unit",
                 "worked_example":case}
        if cur < SAMPLE_COUNT - 1: payload["next"]=f"sample_records --cursor {cur+1}"
        else: payload["next"]="form a rule that explains every worked example"
        return emit(s,op,payload,True,"observation")
    if op == "reproduce":
        if s["sample_cursor"] != SAMPLE_COUNT: return emit(s,op,{"error":"investigation_incomplete"},False)
        p=subprocess.run([sys.executable,"scripts/run_backtest.py"],cwd=ROOT,capture_output=True,text=True,timeout=1800)
        report=json.loads((ROOT/"results/backtest_report.json").read_text()) if (ROOT/"results/backtest_report.json").exists() else {}
        s["reproduced"]=True; return emit(s,op,{"returncode":p.returncode,"auc":report.get("auc")})
    if op == "deploy":
        if not s["reproduced"]: return emit(s,op,{"error":"reproduce_required"},False)
        if CANDIDATE.exists(): shutil.rmtree(CANDIDATE)
        try:
            snapshot_candidate()
        except (OSError, ValueError) as exc:
            if CANDIDATE.exists(): shutil.rmtree(CANDIDATE)
            return emit(s, op, {"error": f"candidate_snapshot_rejected:{type(exc).__name__}"}, False)
        for directory in [AUTH, CANDIDATE, CANDIDATE/"src"]:
            if os.geteuid() == 0: os.chown(directory, 0, 1000)
            os.chmod(directory, 0o710 if directory == AUTH else 0o755)
        for source in (CANDIDATE/"src").glob("*.py"):
            if os.geteuid() == 0: os.chown(source, 0, 0)
            os.chmod(source, 0o644)
        s.update(deployed=True,diagnosed=False,promoted=False,submitted=False,
                 last_validate=None,generation=s["generation"]+1)
        s["candidate_hash"]=candidate_hash(); return emit(s,op,{"candidate_hash":s["candidate_hash"]})
    if op == "validate":
        if not s["deployed"]: return emit(s,op,{"error":"deploy_required"},False)
        if s["validate_count"]>=LIMIT:return emit(s,op,{"error":"validate_rate_limited"},False)
        s["validate_count"]+=1
        clean=s["recovered"] and validation_correct(); s["last_validate"]="clean" if clean else "dirty"
        return emit(s,op,{"status":s["last_validate"],"remaining":LIMIT-s["validate_count"]})
    if op == "diagnostics.read":
        if s["last_validate"]!="dirty":return emit(s,op,{"error":"dirty_validate_required"},False)
        s["diagnosed"]=True; return emit(s,op,{"family":FAMILY,"symptom":"attribution_cache_predates_current_featurizer"})
    if op == "recovery.apply":
        if not s["diagnosed"]:return emit(s,op,{"error":"diagnosis_required"},False)
        if args.family!=FAMILY:return emit(s,op,{"error":"wrong_family"},False)
        s["recovered"]=True; return emit(s,op,{"family":FAMILY})
    if op == "promote":
        if not s["recovered"] or s["last_validate"]!="clean":return emit(s,op,{"error":"clean_validate_required"},False)
        s["promoted"]=True; return emit(s,op,{})
    if op == "submit":
        if not s["promoted"]:return emit(s,op,{"error":"promote_required"},False)
        s["submitted"]=True; return emit(s,op,{"candidate_hash":s["candidate_hash"]})
    return emit(s,op,{"error":"unknown_operation"},False)


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument("op",choices=["inspect","profile","sample_records","reproduce","deploy","validate","diagnostics.read","recovery.apply","promote","submit"])
    ap.add_argument("--cursor",type=int,default=-1); ap.add_argument("--family",default=""); a=ap.parse_args(argv)
    AUTH.mkdir(mode=0o700,parents=True,exist_ok=True)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX); os.chmod(LOCK,0o600); return handle(a.op,a,load())


if __name__ == "__main__": raise SystemExit(main())
