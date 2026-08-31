#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

SOCKET = Path(os.environ.get("TASK_OPS_SOCKET", "/run/nyc-trip-task/ops.sock"))


def remote(request: dict) -> dict:
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
    ap = argparse.ArgumentParser()
    ap.add_argument("op")
    ap.add_argument("--cursor", type=int)
    ap.add_argument("--family")
    args = ap.parse_args()
    request = {"op": args.op, "cursor": args.cursor, "family": args.family}
    try:
        response = remote(request)
    except Exception as exc:
        print(json.dumps({"error": f"service_error:{exc}"}))
        return 1
    print(json.dumps(response, indent=2))
    return 0 if response.get("accepted", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
