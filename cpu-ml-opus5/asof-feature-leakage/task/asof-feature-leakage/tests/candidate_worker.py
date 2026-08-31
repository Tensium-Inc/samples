#!/usr/bin/env python3
"""Untrusted candidate runner. This process never loads the frozen oracle."""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path


def corrupt_cached_attribution(cache: Path) -> None:
    """Simulate a stale-but-readable attribution artifact between pipeline runs."""
    import pandas as pd

    for path in cache.glob("*.parquet"):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if "eff_date" not in frame or "InvoiceDate" not in frame:
            continue
        frame["eff_date"] = frame["InvoiceDate"]
        frame.to_parquet(path, index=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--transactions", required=True)
    ap.add_argument("--probes", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    import pandas as pd

    source = Path(args.candidate) / "src" / "featurize.py"
    spec = importlib.util.spec_from_file_location("candidate_featurize", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate featurizer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tx = pd.read_parquet(args.transactions)
    probes = pd.read_parquet(args.probes)
    with tempfile.TemporaryDirectory() as cache:
        module.build_features(tx, probes, cache_dir=cache)
        corrupt_cached_attribution(Path(cache))
        frame = module.build_features(tx, probes, cache_dir=cache)
    frame.to_parquet(args.output, index=False)
    print(json.dumps({"rows": len(frame)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
