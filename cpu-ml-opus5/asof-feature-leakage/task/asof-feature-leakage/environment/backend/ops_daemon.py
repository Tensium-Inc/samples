#!/usr/bin/env python3
"""Root-owned Unix-socket service for the gated operation broker."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
from pathlib import Path

BACKEND_PATH = Path("/opt/task/ops_backend.py")
BACKEND_SPEC = importlib.util.spec_from_file_location("asof_ops_backend", BACKEND_PATH)
if BACKEND_SPEC is None or BACKEND_SPEC.loader is None:
    raise SystemExit("operation broker backend could not be loaded")
ops_backend = importlib.util.module_from_spec(BACKEND_SPEC)
BACKEND_SPEC.loader.exec_module(ops_backend)

SOCKET_DIR = Path("/run/asof-task")
SOCKET_PATH = SOCKET_DIR / "ops.sock"
MAX_REQUEST = 16_384


def respond(connection: socket.socket) -> None:
    request = b""
    while not request.endswith(b"\n"):
        chunk = connection.recv(4096)
        if not chunk:
            break
        request += chunk
        if len(request) > MAX_REQUEST:
            raise ValueError("request exceeded limit")
    payload = json.loads(request)
    argv = payload.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        raise ValueError("argv must be a string list")
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        try:
            returncode = int(ops_backend.main(argv))
        except SystemExit as exc:
            returncode = int(exc.code) if isinstance(exc.code, int) else 2
    response = {"returncode": returncode, "output": output.getvalue()}
    connection.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("operation broker must start as root")
    SOCKET_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o666)
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection:
                try:
                    respond(connection)
                except Exception as exc:
                    response = {"returncode": 125, "output": f"operation broker error: {type(exc).__name__}\n"}
                    connection.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")


if __name__ == "__main__":
    main()
