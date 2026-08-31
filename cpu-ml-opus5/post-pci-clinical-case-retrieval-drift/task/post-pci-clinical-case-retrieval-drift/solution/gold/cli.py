"""Reference-compatible candidate CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifact_loader import load_cases, load_request
from .ranker import rank_cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--request-stdin", action="store_true")
    args = parser.parse_args()
    if args.request_stdin:
        request = json.load(sys.stdin)
    elif args.request:
        request = load_request(args.request)
    else:
        parser.error("one of --request or --request-stdin is required")
    print(json.dumps(rank_cases(load_cases(args.artifact_root), request), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
