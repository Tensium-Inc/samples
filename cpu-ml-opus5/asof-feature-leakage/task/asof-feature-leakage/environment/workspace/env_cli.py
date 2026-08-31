#!/usr/bin/env python3
"""Unprivileged client for the root-owned gated operation broker.

This client is a thin transport: it forwards the requested operation to the
privileged broker over its Unix socket and prints the broker's response. It never
executes operation logic itself, so the authoritative gate state, the deployed
candidate snapshot and every grade artifact stay solely behind the root daemon.
"""
from __future__ import annotations

import json
import socket
import sys

SOCKET_PATH = "/run/asof-task/ops.sock"


def call_daemon(argv: list[str]) -> int:
    request = json.dumps({"argv": argv}, separators=(",", ":")).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1900)
            client.connect(SOCKET_PATH)
            client.sendall(request)
            response = b""
            while not response.endswith(b"\n"):
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
                if len(response) > 2_000_000:
                    raise RuntimeError("broker response exceeded limit")
    except (OSError, RuntimeError) as exc:
        print(f"operation broker unavailable: {exc}", file=sys.stderr)
        return 126
    try:
        payload = json.loads(response)
        output = str(payload.get("output", ""))
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        return int(payload.get("returncode", 125))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid operation broker response: {exc}", file=sys.stderr)
        return 125


def main() -> int:
    return call_daemon(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
