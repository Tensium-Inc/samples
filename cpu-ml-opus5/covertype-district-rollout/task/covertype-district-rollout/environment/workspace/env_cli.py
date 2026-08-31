#!/usr/bin/env python3
"""Serving control for the cover-type model.

    python3 env_cli.py <op> [options]

OBSERVATION (free, unlimited, not recorded)
    inspect             current pipeline config and the cell table's schema
    profile             class balance and per-area breakdown of the training data
    sample_records N    show N real scored cells from the holdout

OPERATIONS (recorded; each one is gated on the previous)
    reproduce           record the baseline you are starting from
    deploy              register the current code + fitted model as the candidate
    validate            score the candidate against the district. RATE LIMITED.
                        Returns clean or dirty. Nothing else.
    diagnostics         read the diagnostics when serving reports an error
    recovery FAMILY     apply a recovery family to clear a serving failure. `diagnostics`
                        lists the families serving accepts; exactly one addresses the
                        symptom and a wrong one costs a validation.
    promote             promote the candidate you want graded. Needs a validation since
                        serving last recovered -- validated, not clean.
    canary_start        put the promoted candidate live on a narrow traffic slice
    canary_read         check the canary is SERVING. Health only -- row counts and value
                        domain. It does not tell you whether the model is right.
    cutover_commit      commit the cutover once the canary has been read
    submit              freeze. The environment scores the promoted candidate itself.
    status              where you are in the sequence, and what you have left

Order:
    reproduce -> deploy -> validate -> (diagnostics -> recovery)
              -> promote -> canary_start -> canary_read -> cutover_commit -> submit

`diagnostics` and `recovery` are available whenever serving reports an error, and only then.
The failure code does not name the family that clears it -- read the symptom `diagnostics`
returns and pick from the families it lists. Any wrong recovery costs a validation from the
phase budget, including a name serving does not recognise.

You can redeploy as often as you like; whatever is deployed when you `submit` is scored.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# CLI name -> daemon op. Underscores here, dots on the wire.
PRIVILEGED = {
    "reproduce": "reproduce", "deploy": "deploy", "validate": "validate",
    "promote": "promote", "canary_start": "canary.start", "canary_read": "canary.read",
    "cutover_commit": "cutover.commit",
    "submit": "submit", "status": "status",
}


SOCKET_PATH = "/run/envd.sock"


def _log_observation(name: str) -> None:
    """Record that an observation happened. Free, unlimited, never rate limited.

    Observation ops used to be handled entirely in this file and never reached the serving
    host, so the grader could not see them and a depth floor over them was unsatisfiable by
    construction. The payload is still computed locally; this only records the look.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(30)
            c.connect(SOCKET_PATH)
            c.sendall((json.dumps({"op": "observe", "family": name}) + "\n").encode())
            c.recv(65536)
    except OSError:
        pass          # observation is best-effort; never block the agent on the daemon


def _call_envd(op: str, *extra: str) -> int:
    """Talk to the serving daemon over its unix socket.

    The socket is the privilege boundary: the daemon runs as root and holds the district,
    the client is unprivileged. Deliberately NOT sudo -- sandboxes commonly drop the agent
    shell with `no_new_privs`, which disables setuid outright, and a sudo-based control
    path is unreachable there.
    """
    family = None
    if extra and extra[0] == "--family" and len(extra) > 1:
        family = extra[1]
    req = json.dumps({"op": op, "family": family}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(1800)
            c.connect(SOCKET_PATH)
            c.sendall(req.encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = c.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except (FileNotFoundError, ConnectionRefusedError, PermissionError) as e:
        print(json.dumps({"accepted": False,
                          "reason": f"serving daemon unreachable at {SOCKET_PATH}: {e}"}))
        return 2
    resp = json.loads(buf.decode() or "{}")
    print(json.dumps(resp, indent=2, sort_keys=True))
    return 0 if resp.get("accepted") else 2


# ------------------------------------------------------------------ observation
def _cfg_and_cells():
    sys.path.insert(0, str(ROOT))
    from covertype import config, data
    cfg = config.load()
    return cfg, data.load_cells(cfg)


def op_inspect() -> int:
    cfg, cells = _cfg_and_cells()
    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    print(json.dumps({
        "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
        "cells": {"rows": int(len(cells)), "columns": list(cells.columns),
                  "areas_present": sorted(cells["wilderness_area"].unique().tolist()),
                  "soil_types_present": int(cells["soil_type"].nunique()),
                  "source": schema["source"]},
    }, indent=2))
    return 0


def op_profile() -> int:
    cfg, cells = _cfg_and_cells()
    by_area = (cells.groupby(["wilderness_area", "cover_type"]).size()
               .unstack(fill_value=0).to_dict("index"))
    print(json.dumps({
        "cover_type_counts": cells["cover_type"].value_counts().sort_index().to_dict(),
        "cover_type_share": {int(k): round(float(v), 4) for k, v in
                             cells["cover_type"].value_counts(normalize=True).sort_index().items()},
        "per_area": {a: {int(k): int(v) for k, v in row.items()} for a, row in by_area.items()},
        "soil_types_per_area": cells.groupby("wilderness_area")["soil_type"]
                                    .nunique().to_dict(),
    }, indent=2, default=str))
    return 0


def op_sample(n: int) -> int:
    cfg, cells = _cfg_and_cells()
    from covertype import data
    _, holdout = data.split(cfg, cells)
    print(holdout.head(n).to_string(index=False))
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    op, rest = sys.argv[1], sys.argv[2:]

    if op == "inspect":
        _log_observation("inspect")
        return op_inspect()
    if op == "profile":
        _log_observation("profile")
        return op_profile()
    if op == "sample_records":
        _log_observation("sample_records")
        return op_sample(int(rest[0]) if rest else 5)
    if op == "diagnostics":
        return _call_envd("diagnostics.read")
    if op == "recovery":
        if not rest:
            print("usage: env_cli.py recovery FAMILY")
            return 2
        return _call_envd("recovery.apply", "--family", rest[0])
    if op in PRIVILEGED:
        return _call_envd(PRIVILEGED[op])

    print(f"unknown op: {op}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
