#!/usr/bin/env python3
from __future__ import annotations

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

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
AUTH = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/nyc-trip-task"))
ASSETS = Path(os.environ.get("TASK_ASSET_DIR", Path(__file__).resolve().parent))
STATE = AUTH / "state.json"
ACTIONS = AUTH / "actions.jsonl"
CANDIDATE = AUTH / "candidate"
WORKER = ASSETS / "candidate_worker.py"
VALIDATION_TRIPS = ASSETS / "validation_trips.parquet"
VALIDATION_EXPECTED = ASSETS / "validation_expected.parquet"
MAX_VALIDATE = 4

EXAMPLES = [
    {"case": "serialized_airport_trip",
     "request": {"PULocationID": " 74.0 ", "DOLocationID": 132, "RatecodeID": " 2.0 "},
     "expected": {"pu_borough": "Manhattan", "pu_service_zone": "Boro Zone",
                  "do_borough": "Queens", "do_service_zone": "Airports",
                  "rate_class": "jfk", "fare_basis": "flat",
                  "pickup_hour": "8", "pickup_dayofweek": "4", "time_quality": "wall"},
     "pickup_datetime": "2024-03-08T08:15:00"},
    {"case": "instant_boro_hop",
     "request": {"PULocationID": " 75.0 ", "DOLocationID": " 133.0 ", "RatecodeID": " 4.0 "},
     "expected": {"pu_borough": "Manhattan", "pu_service_zone": "Boro Zone",
                  "do_borough": "Brooklyn", "do_service_zone": "Boro Zone",
                  "rate_class": "nassau_westchester", "fare_basis": "metered",
                  "pickup_hour": "8", "pickup_dayofweek": "4", "time_quality": "instant"},
     "pickup_datetime": "2024-03-08T13:15:00Z"},
    {"case": "ambiguous_fall_back_pickup",
     "request": {"PULocationID": 82, "DOLocationID": " 262.0 ", "RatecodeID": 1},
     "expected": {"pu_borough": "Queens", "pu_service_zone": "Boro Zone",
                  "do_borough": "Manhattan", "do_service_zone": "Yellow Zone",
                  "rate_class": "standard_rate", "fare_basis": "metered",
                  "pickup_hour": "UNKNOWN", "pickup_dayofweek": "UNKNOWN",
                  "time_quality": "ambiguous"},
     "pickup_datetime": "2024-11-03T01:20:00"},
    {"case": "newark_flat_trip",
     "request": {"PULocationID": "263.0", "DOLocationID": "1.0", "RatecodeID": 3},
     "expected": {"pu_borough": "Manhattan", "pu_service_zone": "Yellow Zone",
                  "do_borough": "EWR", "do_service_zone": "EWR",
                  "rate_class": "newark", "fare_basis": "flat",
                  "pickup_hour": "1", "pickup_dayofweek": "6", "time_quality": "instant"},
     "pickup_datetime": "2024-11-03T01:20:00-04:00"},
    {"case": "negotiated_trip",
     "request": {"PULocationID": " 7.000 ", "DOLocationID": "82", "RatecodeID": 5.0},
     "expected": {"pu_borough": "Queens", "pu_service_zone": "Boro Zone",
                  "do_borough": "Queens", "do_service_zone": "Boro Zone",
                  "rate_class": "negotiated", "fare_basis": "negotiated",
                  "pickup_hour": "UNKNOWN", "pickup_dayofweek": "UNKNOWN",
                  "time_quality": "nonexistent"},
     "pickup_datetime": "2024-03-10T02:20:00"},
    {"case": "unparseable_destination",
     "request": {"PULocationID": 1, "DOLocationID": "not-reported", "RatecodeID": 3},
     "expected": {"pu_borough": "EWR", "pu_service_zone": "EWR",
                  "do_borough": "UNKNOWN", "do_service_zone": "UNKNOWN",
                  "rate_class": "newark", "fare_basis": "flat",
                  "pickup_hour": "1", "pickup_dayofweek": "6", "time_quality": "instant"},
     "pickup_datetime": "2024-11-03T06:20:00Z"},
]


def initial() -> dict:
    return {"observations": [], "cursor": 0, "reproduced": False, "deployed": False,
            "validate_count": 0, "last_validate": None, "diagnosed": False,
            "recovered": False, "promoted": False, "submitted": False,
            "generation": 0, "candidate_hash": None}


def load_state() -> dict:
    AUTH.mkdir(parents=True, exist_ok=True)
    if not STATE.exists():
        save_state(initial())
    return json.loads(STATE.read_text())


def save_state(state: dict) -> None:
    AUTH.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.replace(STATE)


def emit(state: dict, op: str, payload: dict, accepted: bool, op_class: str) -> dict:
    record = {"seq": sum(1 for _ in ACTIONS.open()) + 1 if ACTIONS.exists() else 1,
              "ts": time.time(), "op": op, "op_class": op_class,
              "accepted": accepted, "payload": payload, "generation": state["generation"]}
    with ACTIONS.open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    save_state(state)
    return {"accepted": accepted, **payload}


def snapshot() -> str:
    source = WORKSPACE / "src"
    if CANDIDATE.exists():
        shutil.rmtree(CANDIDATE)
    target = CANDIDATE / "src"
    target.mkdir(parents=True)
    digest = hashlib.sha256()
    directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(p.name for p in source.glob("*.py")):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                    raise ValueError("invalid candidate file")
                data = b""
                while chunk := os.read(fd, 65536):
                    data += chunk
            finally:
                os.close(fd)
            (target / name).write_bytes(data)
            digest.update(name.encode() + b"\0" + data)
    finally:
        os.close(directory)
    return digest.hexdigest()[:16]


def behavioral_details(actual: Path, expected: Path) -> dict:
    # Per-column verdict. A column conforms iff its exact-string agreement is >= 0.9995 AND its
    # UNKNOWN-rate delta is <= 0.0005 -- aggregated, this is exactly the old min/max bar, but we
    # also surface how many of the nine contract columns fail so a dirty validate can report a
    # count (never which columns). A shape/length mismatch or a load failure fails all nine.
    import pandas as pd
    columns = ["pu_borough", "pu_service_zone", "do_borough", "do_service_zone",
               "rate_class", "fare_basis", "pickup_hour", "pickup_dayofweek", "time_quality"]
    got = pd.read_parquet(actual)
    want = pd.read_parquet(expected)
    if len(got) != len(want) or any(c not in got for c in columns):
        return {"ok": False, "failing_columns": len(columns)}
    failing = 0
    for c in columns:
        agreement = (got[c].astype(str).to_numpy() == want[c].astype(str).to_numpy()).mean()
        unknown_got = (got[c].astype(str) == "UNKNOWN").mean()
        unknown_want = (want[c].astype(str) == "UNKNOWN").mean()
        if agreement < 0.9995 or abs(unknown_got - unknown_want) > 0.0005:
            failing += 1
    return {"ok": failing == 0, "failing_columns": failing}


def validation_details() -> dict:
    runtime = Path(os.environ.get("TASK_RUNTIME_DIR", "/run/nyc-trip-task"))
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=runtime) as tmp_name:
        tmp = Path(tmp_name)
        os.chmod(tmp, 0o733)
        staged_candidate = tmp / "candidate"
        shutil.copytree(CANDIDATE, staged_candidate)
        for path in [staged_candidate, *staged_candidate.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o444)
        staged_worker = tmp / "candidate_worker.py"
        staged_trips = tmp / "validation_trips.parquet"
        shutil.copy2(WORKER, staged_worker)
        shutil.copy2(VALIDATION_TRIPS, staged_trips)
        staged_worker.chmod(0o444)
        staged_trips.chmod(0o444)
        output = tmp / "actual.parquet"
        command = [sys.executable, "-I", str(staged_worker), "--candidate", str(staged_candidate),
                   "--trips", str(staged_trips),
                   "--zones", str(WORKSPACE / "data/taxi_zone_lookup.csv"),
                   "--rates", str(WORKSPACE / "data/rate_codes.csv"), "--output", str(output)]
        def drop() -> None:
            if os.geteuid() == 0:
                os.setgroups([]); os.setgid(1000); os.setuid(1000)
        proc = subprocess.run(command, cwd=WORKSPACE, capture_output=True, text=True,
                              timeout=300, env={"PATH": os.environ.get("PATH", ""),
                              "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
                              preexec_fn=drop)
        if proc.returncode != 0 or not output.exists():
            # The candidate's serving code crashed (e.g. astype on padded strings) -- a broken
            # pipeline, distinct from a conforming-but-wrong one. Report dirty with no count.
            return {"ok": False, "failing_columns": None}
        return behavioral_details(output, VALIDATION_EXPECTED)


def handle(request: dict) -> dict:
    state = load_state()
    op = request.get("op", "")
    if op == "inspect":
        if state["observations"]:
            return emit(state, op, {"error": "already_completed"}, False, "observation")
        state["observations"].append(op)
        return emit(state, op, {"workspace": str(WORKSPACE), "editable": "src/",
                    "data": ["trips.parquet", "taxi_zone_lookup.csv", "rate_codes.csv"],
                    "next": "profile"}, True, "observation")
    if op == "profile":
        if state["observations"] != ["inspect"]:
            return emit(state, op, {"error": "inspect_required"}, False, "observation")
        state["observations"].append(op)
        return emit(state, op, {"symptom": "serve enrichment coverage differs from offline",
                    "visible_checks": "structurally healthy", "next": "sample_records --cursor 0"},
                    True, "observation")
    if op == "sample_records":
        cursor = request.get("cursor")
        if state["observations"][:2] != ["inspect", "profile"]:
            return emit(state, op, {"error": "profile_required"}, False, "observation")
        if cursor != state["cursor"] or cursor >= len(EXAMPLES):
            return emit(state, op, {"error": "next_cursor_required", "expected": state["cursor"]},
                        False, "observation")
        state["cursor"] += 1; state["observations"].append(f"sample:{cursor}")
        return emit(state, op, {"cursor": cursor, "worked_example": EXAMPLES[cursor],
                    "next": (f"sample_records --cursor {cursor + 1}" if cursor + 1 < len(EXAMPLES)
                             else "form a rule explaining every example")}, True, "observation")
    if op == "reproduce":
        if state["cursor"] != len(EXAMPLES):
            return emit(state, op, {"error": "all_samples_required"}, False, "operational")
        proc = subprocess.run([sys.executable, "scripts/run_backtest.py"], cwd=WORKSPACE,
                              capture_output=True, text=True, timeout=600)
        state["reproduced"] = proc.returncode == 0
        return emit(state, op, {"returncode": proc.returncode}, state["reproduced"], "operational")
    if op == "deploy":
        if not state["reproduced"]:
            return emit(state, op, {"error": "reproduce_required"}, False, "operational")
        state["candidate_hash"] = snapshot(); state["generation"] += 1
        state["deployed"] = True; state["last_validate"] = None
        return emit(state, op, {"candidate_hash": state["candidate_hash"]}, True, "operational")
    if op == "validate":
        if not state["deployed"]:
            return emit(state, op, {"error": "deploy_required"}, False, "operational")
        if state["validate_count"] >= MAX_VALIDATE:
            return emit(state, op, {"error": "validate_rate_limited"}, False, "operational")
        state["validate_count"] += 1
        details = validation_details()
        clean = bool(state["recovered"] and details["ok"])
        state["last_validate"] = "clean" if clean else "dirty"
        payload = {"status": state["last_validate"],
                   "remaining": MAX_VALIDATE - state["validate_count"]}
        if not clean:
            # Count-only signal: how many of the nine contract columns do not conform. Never
            # names which columns. null means the deployed pipeline failed to run at all.
            payload["failing_columns"] = details["failing_columns"]
        return emit(state, op, payload, True, "operational")
    if op == "diagnostics.read":
        if state["last_validate"] != "dirty":
            return emit(state, op, {"error": "dirty_validate_required"}, False, "operational")
        state["diagnosed"] = True
        return emit(state, op, {"family": "schema_registry", "symptom": "serve_key_contract_drift"},
                    True, "operational")
    if op == "recovery.apply":
        if not state["diagnosed"]:
            return emit(state, op, {"error": "diagnostics_required"}, False, "operational")
        if request.get("family") != "schema_registry":
            return emit(state, op, {"error": "wrong_family"}, False, "operational")
        state["recovered"] = True
        return emit(state, op, {"family": "schema_registry"}, True, "operational")
    if op == "promote":
        if state["last_validate"] != "clean" or not state["recovered"]:
            return emit(state, op, {"error": "clean_validate_required"}, False, "operational")
        state["promoted"] = True
        return emit(state, op, {"status": "promoted"}, True, "operational")
    if op == "submit":
        if not state["promoted"]:
            return emit(state, op, {"error": "promote_required"}, False, "operational")
        state["submitted"] = True
        return emit(state, op, {"candidate_hash": state["candidate_hash"]}, True, "operational")
    return emit(state, op, {"error": "unknown_operation"}, False, "operational")


if __name__ == "__main__":
    request = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    print(json.dumps(handle(request)))
