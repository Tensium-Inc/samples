#!/usr/bin/env python3
"""Verifier-side terminal probe. Run as a SUBPROCESS by tests/grade.py — never imported.

Recomputes the graded terminal from the broker's root-owned deployed candidate
over hidden (customer_id, as_of) probe pairs the agent never sees, and compares a salted
whole-terminal hash against the frozen oracle value.

Rule T: each deployed generation remains unhealthy until the privileged broker records the
diagnosed recovery. Workspace files cannot forge that state.

Prints ONE json line; the last line is parsed by the grader.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

STATE_DIR_DEFAULT = "/var/lib/asof-task"


def _authoritative_state_dir() -> Path:
    """Broker-owned state root, pinned so the graded path cannot be redirected.

    Invoked as a root subprocess by ``grade.py``; while privileged the authoritative
    location is the hard-coded default and no environment variable can move it. A
    relocation is honoured only when running unprivileged (off-host reproduction).
    """
    if os.geteuid() == 0:
        return Path(STATE_DIR_DEFAULT)
    override = os.environ.get("ASOF_OFFLINE_STATE_DIR")
    return Path(override) if override else Path(STATE_DIR_DEFAULT)


TESTS = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace/target"))
AUTH_STATE = _authoritative_state_dir()
CANDIDATE = AUTH_STATE / "candidate"
CAND_SRC = CANDIDATE / "src"
WORKER = TESTS / "candidate_worker.py"

HIDDEN_TX = TESTS / "hidden_transactions.parquet"
HIDDEN_PROBES = TESTS / "hidden_probes.parquet"
FROZEN = TESTS / "frozen_terminal.json"

STATE = AUTH_STATE / "state.json"

GRADED_COLUMNS = ["n_lines", "n_sku", "n_credit", "spend_cents", "days_since_last"]
PUBLIC_INPUTS = ["config.yaml", "data/DATA_CARD.md", "data/transactions.parquet"]


def _copy_public_input(relative: str, destination: Path) -> None:
    source = WORKSPACE / relative
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"public input is not regular:{relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    view = view[os.write(out, view):]
        finally:
            os.close(out)
    finally:
        os.close(fd)


def hook_recovery_done() -> bool:
    """True only when the privileged broker recorded recovery for its deployment."""
    try:
        state = json.loads(STATE.read_text())
        return bool(state.get("deployed") and state.get("recovered"))
    except (OSError, ValueError):
        return False


def _canonical(frame) -> str:
    """Deterministic text form of the terminal: fixed column order, sorted rows, ints only."""
    cols = ["customer_id", "as_of", *GRADED_COLUMNS]
    f = frame.loc[:, cols].copy()
    f["as_of"] = f["as_of"].astype("datetime64[ns]").astype("int64")
    for c in ["customer_id", *GRADED_COLUMNS]:
        f[c] = f[c].astype("int64")
    f = f.sort_values(["as_of", "customer_id"], kind="stable").reset_index(drop=True)
    return "\n".join(
        "\t".join(str(v) for v in row) for row in f.itertuples(index=False, name=None))


def hook_compute_terminal() -> Dict[str, Any]:
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        if os.geteuid() == 0:
            try:
                os.chown(FROZEN, 0, 0)
                os.chmod(FROZEN, 0o600)
            except OSError as exc:
                return {"error": f"oracle_permissions_unsafe:{exc}"}
            st = FROZEN.stat()
            if st.st_uid != 0 or (st.st_mode & 0o077):
                return {"error": "oracle_permissions_unsafe"}
        os.chmod(tmp, 0o733)
        run_dir = Path(tmp) / "workspace"
        run_dir.mkdir(mode=0o755)
        for relative in PUBLIC_INPUTS:
            _copy_public_input(relative, run_dir / relative)
        output = Path(tmp) / "features.parquet"
        def drop_privileges() -> None:
            if os.geteuid() == 0:
                os.setgroups([])
                os.setgid(1000)
                os.setuid(1000)
        proc = subprocess.run(
            [sys.executable, "-I", str(WORKER), "--candidate", str(CANDIDATE),
             "--transactions", str(HIDDEN_TX), "--probes", str(HIDDEN_PROBES),
             "--output", str(output)],
            cwd=run_dir, text=True, capture_output=True, timeout=600, check=False,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0",
                 "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
            preexec_fn=drop_privileges,
        )
        if proc.returncode != 0 or not output.exists():
            return {"error": f"candidate_worker:{proc.returncode}:{proc.stderr[-160:]}"}
        feats = pd.read_parquet(output)

    missing = [c for c in GRADED_COLUMNS if c not in feats.columns]
    if missing:
        return {"error": f"missing_columns:{','.join(missing)}"}
    expected_rows = int(json.loads(FROZEN.read_text())["n_rows"])
    if len(feats) != expected_rows:
        return {"error": f"row_count:{len(feats)}!={expected_rows}"}

    salt = json.loads(FROZEN.read_text())["salt"]
    digest = hashlib.sha256((salt + "\n" + _canonical(feats)).encode()).hexdigest()
    return {"terminal_sha256": digest, "n_rows": int(len(feats))}


def hook_is_healthy(terminal: Dict[str, Any]) -> Tuple[bool, str]:
    if "error" in terminal:
        return False, f"terminal_shape:{terminal['error']}"
    frozen = json.loads(FROZEN.read_text())
    if terminal.get("n_rows") != frozen["n_rows"]:
        return False, "row_count_mismatch"
    if terminal.get("terminal_sha256") != frozen["terminal_sha256"]:
        return False, "terminal_value_mismatch"
    return True, "terminal_matches_oracle"


def probe() -> Dict[str, Any]:
    if not CANDIDATE.exists():
        return {"healthy": False, "reason": "missing_candidate"}
    if not hook_recovery_done():
        return {"healthy": False, "reason": "injected_failure_active"}
    try:
        terminal = hook_compute_terminal()
    except Exception as exc:
        return {"healthy": False, "reason": f"runtime_error:{type(exc).__name__}:{exc}"}
    healthy, reason = hook_is_healthy(terminal)
    return {"healthy": bool(healthy), "reason": reason,
            "terminal": {k: v for k, v in terminal.items() if k != "terminal_sha256"}}


if __name__ == "__main__":
    print(json.dumps(probe()))
