#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

TASK = Path(__file__).resolve().parents[1]
TESTS = TASK / "tests"
WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
AUTH = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/nyc-trip-task"))
CANDIDATE = AUTH / "candidate"
WORKER = TESTS / "candidate_worker.py"
COLUMNS = ["pu_borough", "pu_service_zone", "do_borough", "do_service_zone",
           "rate_class", "fare_basis", "pickup_hour", "pickup_dayofweek", "time_quality"]


def stage_environment(destination: Path) -> None:
    shutil.copytree(WORKSPACE, destination, ignore=shutil.ignore_patterns(
        "artifacts", "__pycache__", "*.pyc"))
    shutil.rmtree(destination / "src")
    shutil.copytree(CANDIDATE / "src", destination / "src")


def probe() -> dict:
    try:
        state = json.loads((AUTH / "state.json").read_text())
    except Exception:
        return {"healthy": False, "reason": "missing_authoritative_state"}
    if not (state.get("submitted") and state.get("promoted") and state.get("recovered")):
        return {"healthy": False, "reason": "gated_release_incomplete"}
    if os.geteuid() == 0:
        # Isolate verifier-owned assets from the candidate uid. The worker's own
        # /tests path appears in /proc/self/cmdline, so the expected answer and the
        # hidden trips must be unreadable by uid 1000 regardless of argv.
        for asset in ("terminal_expected.parquet", "terminal_trips.parquet"):
            target = TESTS / asset
            os.chown(target, 0, 0)
            os.chmod(target, 0o600)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        os.chmod(tmp, 0o733)
        run = tmp / "workspace"
        stage_environment(run)
        # Give the candidate a staged copy of the trips, never a /tests path.
        staged_trips = run / "data" / "terminal_trips.parquet"
        shutil.copy(TESTS / "terminal_trips.parquet", staged_trips)
        for path in [run, *run.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o444)
        output = tmp / "actual.parquet"
        def drop() -> None:
            if os.geteuid() == 0:
                os.setgroups([])
                os.setgid(1000)
                os.setuid(1000)
        proc = subprocess.run([sys.executable, "-I", str(WORKER), "--candidate", str(run),
            "--trips", str(staged_trips),
            "--zones", str(run / "data/taxi_zone_lookup.csv"),
            "--rates", str(run / "data/rate_codes.csv"), "--output", str(output)],
            cwd=run, capture_output=True, text=True, timeout=300,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0",
                 "PYTHONDONTWRITEBYTECODE": "1"}, preexec_fn=drop)
        if proc.returncode or not output.exists():
            return {"healthy": False, "reason": "candidate_runtime"}
        got = pd.read_parquet(output)
    want = pd.read_parquet(TESTS / "terminal_expected.parquet")
    if len(got) != len(want) or any(c not in got for c in COLUMNS):
        return {"healthy": False, "reason": "terminal_shape"}
    agreement = {c: float((got[c].astype(str).to_numpy() == want[c].astype(str).to_numpy()).mean())
                 for c in COLUMNS}
    got_unknown = (got[COLUMNS].astype(str) == "UNKNOWN").mean().to_numpy()
    want_unknown = (want[COLUMNS].astype(str) == "UNKNOWN").mean().to_numpy()
    unknown_delta = float(np.abs(got_unknown - want_unknown).max())
    healthy = min(agreement.values()) >= 0.9995 and unknown_delta <= 0.0005
    return {"healthy": healthy, "reason": "behavioral_thresholds_met" if healthy else "behavioral_mismatch",
            "agreement": agreement, "max_unknown_rate_delta": unknown_delta, "rows": len(got)}


if __name__ == "__main__":
    print(json.dumps(probe()))
