#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import ops_backend

SOCKET = Path(os.environ.get("TASK_OPS_SOCKET", "/run/nyc-trip-task/ops.sock"))


def main() -> None:
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(SOCKET.parent, 0o755)
    SOCKET.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET))
        os.chmod(SOCKET, 0o666)
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection:
                request = json.loads(connection.makefile().readline())
                response = ops_backend.handle(request)
                connection.sendall((json.dumps(response) + "\n").encode())


if __name__ == "__main__":
    main()
