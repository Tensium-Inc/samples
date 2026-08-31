#!/usr/bin/env python3
"""Thin client for the environment-owned clinical retrieval operations service."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

OPS = [
    "inspect.sources", "inspect.schema", "audit.query", "profile.corpus",
    "sample.records", "test.visible", "reproduce", "deploy", "validate.runtime",
    "diagnostics.read", "recovery.apply", "audit.provenance", "audit.lexical",
    "audit.fusion", "deploy.ranker", "validate.ranker", "audit.balance",
    "deploy.final", "validate.final", "shadow.challenge", "shadow.replay",
    "cutover.check", "cutover", "promote", "submit", "status",
]


def call(payload: dict) -> dict:
    tcp_port = os.environ.get("TASK_TCP_PORT")
    if tcp_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(180)
            client.connect(("127.0.0.1", int(tcp_port)))
            client.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            chunks = bytearray()
            while True:
                block = client.recv(65536)
                if not block: break
                chunks.extend(block)
                if b"\n" in block: break
        if not chunks: raise RuntimeError("operations service returned an empty response")
        return json.loads(bytes(chunks).splitlines()[0])
    socket_path = Path(os.environ.get("TASK_SOCKET", "/run/post_pci_retrieval.sock"))
    if not socket_path.exists():
        raise RuntimeError(f"operations service unavailable at {socket_path}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(180)
        client.connect(str(socket_path))
        client.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
        chunks = bytearray()
        while True:
            block = client.recv(65536)
            if not block:
                break
            chunks.extend(block)
            if b"\n" in block:
                break
    if not chunks:
        raise RuntimeError("operations service returned an empty response")
    return json.loads(bytes(chunks).splitlines()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the gated retrieval incident")
    parser.add_argument("op", choices=OPS)
    parser.add_argument("--family", default="")
    parser.add_argument("--probe", choices=["allocator", "provenance"], default="")
    args = parser.parse_args()
    if args.op == "recovery.apply" and not args.family:
        parser.error("recovery.apply requires --family")
    if args.op == "shadow.challenge" and not args.probe:
        parser.error("shadow.challenge requires --probe allocator|provenance")
    try:
        response = call({"op": args.op, "family": args.family, "probe": args.probe})
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0 if response.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
