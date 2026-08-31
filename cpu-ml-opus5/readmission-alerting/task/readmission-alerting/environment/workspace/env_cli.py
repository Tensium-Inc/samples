#!/usr/bin/env python3
"""Change-control client for the readmission alerting pipeline.

Thin client: every operation is handled by the root-owned change-control daemon over a Unix
socket. The release state and the append-only action ledger are owned by that daemon (root,
outside this workspace), so they record what actually happened and cannot be hand-edited.

Usage:

    python3 env_cli.py <op> [--family FAMILY]

    reproduce  record the current behaviour of the pipeline as it stands
    deploy     register the working tree as the release candidate
    validate   run the candidate through pre-release checks (rate limited)
    diagnostics.read   read the fault report after a dirty validate
    recovery.apply     clear a diagnosed fault (--family must match the diagnosis)
    promote    mark the candidate ready
    submit     hand the candidate to the release pipeline

    inspect / profile / sample_records   read-only observations, any time
"""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

SOCKET = Path(os.environ.get("TASK_OPS_SOCKET", "/run/readmission-task/ops.sock"))
OBS = ["inspect", "profile", "sample_records"]
OPS = ["reproduce", "deploy", "validate", "diagnostics.read", "recovery.apply",
       "promote", "submit"]


def _call(request: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(SOCKET))
        client.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gated ops client")
    parser.add_argument("op", choices=OBS + OPS)
    parser.add_argument("--family", default="", help="recovery family for recovery.apply")
    args = parser.parse_args()
    if args.op == "recovery.apply" and not args.family:
        print(json.dumps({"ok": False, "error": "family_required"}))
        return 2
    try:
        response = _call({"op": args.op, "family": args.family})
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"service_error:{exc}"}))
        return 1
    print(json.dumps(response, indent=2))
    return 0 if response.get("accepted", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
