#!/usr/bin/env python3
"""Author-side audit for the disjoint validation/hidden holdout.

The in-episode ``validate`` op scores the candidate against
``environment/backend/validation_*.parquet``; the final grader scores it against
``tests/hidden_*.parquet``. Those two slices are a TRUE holdout: disjoint customer sets
(``customer_id`` parity), so the feedback verdict is not a byte-oracle for the grade.

This script recomputes both frozen terminals from the gold featurizer through the shipped
``tests/candidate_worker.py`` (the exact path the grader uses) and asserts:

  * gold reproduces each committed oracle hash / row count   (positive control)
  * the two slices are byte-distinct and cover disjoint customers   (holdout property)

Run:  python3 solution/tools/verify_holdout.py
It reads only shipped author-side material (gold + oracles); it never needs the broker.
Not copied into the environment image (see environment/Dockerfile).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # asof-feature-leakage/
BACKEND = ROOT / "environment" / "backend"
TESTS = ROOT / "tests"
GOLD = ROOT / "solution" / "gold"
WORKER = TESTS / "candidate_worker.py"
GRADED = ["n_lines", "n_sku", "n_credit", "spend_cents", "days_since_last"]

VAL = (BACKEND / "validation_transactions.parquet",
       BACKEND / "validation_probes.parquet",
       BACKEND / "validation_oracle.json")
HID = (TESTS / "hidden_transactions.parquet",
       TESTS / "hidden_probes.parquet",
       TESTS / "frozen_terminal.json")


def _canonical(frame) -> str:
    cols = ["customer_id", "as_of", *GRADED]
    f = frame.loc[:, cols].copy()
    f["as_of"] = f["as_of"].astype("datetime64[ns]").astype("int64")
    for c in ["customer_id", *GRADED]:
        f[c] = f[c].astype("int64")
    f = f.sort_values(["as_of", "customer_id"], kind="stable").reset_index(drop=True)
    return "\n".join("\t".join(str(v) for v in row)
                     for row in f.itertuples(index=False, name=None))


def _gold_terminal(tx: Path, probes: Path):
    import pandas as pd
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cand = td / "candidate" / "src"
        cand.mkdir(parents=True)
        for name in ("featurize.py", "split.py"):
            shutil.copyfile(GOLD / name, cand / name)
        out = td / "features.parquet"
        proc = subprocess.run(
            [sys.executable, "-I", str(WORKER), "--candidate", str(td / "candidate"),
             "--transactions", str(tx), "--probes", str(probes), "--output", str(out)],
            cwd=td, capture_output=True, text=True, timeout=900,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0",
                 "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
        if proc.returncode != 0 or not out.exists():
            raise SystemExit(f"gold worker failed rc={proc.returncode}: {proc.stderr[-400:]}")
        return pd.read_parquet(out)


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def main() -> int:
    import pandas as pd
    ok = True
    for label, (tx, probes, oracle_path) in (("validation", VAL), ("hidden", HID)):
        oracle = json.loads(oracle_path.read_text())
        term = _gold_terminal(tx, probes)
        digest = hashlib.sha256((oracle["salt"] + "\n" + _canonical(term)).encode()).hexdigest()
        match = digest == oracle["terminal_sha256"] and len(term) == oracle["n_rows"]
        ok = ok and match
        print(f"{label:<10} rows={len(term)} (oracle {oracle['n_rows']}) "
              f"gold {'MATCH' if match else 'MISMATCH'}")

    vt, ht = pd.read_parquet(VAL[0]), pd.read_parquet(HID[0])
    vp, hp = pd.read_parquet(VAL[1]), pd.read_parquet(HID[1])
    disjoint = set(vp.customer_id).isdisjoint(set(hp.customer_id)) and \
        set(vt.customer_id).isdisjoint(set(ht.customer_id))
    distinct = _md5(VAL[0]) != _md5(HID[0]) and _md5(VAL[1]) != _md5(HID[1])
    print(f"disjoint customer sets: {disjoint}")
    print(f"byte-distinct slices:   {distinct}")
    ok = ok and disjoint and distinct
    print("HOLDOUT OK" if ok else "HOLDOUT FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
