#!/usr/bin/env python3
"""DRAFT root daemon: owns the release state machine over a Unix socket.

Runs as root (started by entrypoint.sh before the shell drops to uid 1000). The state,
ledger and candidate snapshot it writes live under TASK_STATE_DIR, root-owned 0700, so the
agent cannot forge them. Candidate code is executed only by ops_backend via a uid-1000 child.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import ops_backend

SOCKET = Path(os.environ.get("TASK_OPS_SOCKET", "/run/readmission-task/ops.sock"))


def main() -> None:
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(SOCKET.parent, 0o755)
    ops_backend.AUTH.mkdir(parents=True, exist_ok=True)
    os.chmod(ops_backend.AUTH, 0o700)
    SOCKET.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET))
        os.chmod(SOCKET, 0o666)      # agent may connect; state stays root-owned
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection:
                try:
                    line = connection.makefile().readline()
                    response = ops_backend.handle(json.loads(line))
                except Exception as exc:  # noqa: BLE001
                    response = {"ok": False, "accepted": False, "error": f"daemon_error:{exc}"}
                connection.sendall((json.dumps(response) + "\n").encode())


if __name__ == "__main__":
    main()
