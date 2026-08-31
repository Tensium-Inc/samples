#!/usr/bin/env python3
"""Run the readmission evaluation and write the report the care team reads.

    python3 scripts/run_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config, resolve       # noqa: E402
from src.evaluate import run_evaluation           # noqa: E402
from src.load import load_encounters              # noqa: E402


def main() -> int:
    cfg = load_config()
    df = load_encounters(cfg)
    result = run_evaluation(df, cfg)

    out = resolve(cfg["reporting"]["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))

    print(f"encounters : {len(df)}")
    print(f"folds      : {result['n_splits']}")
    print(f"{result['metric']:11s}: {result['score']:.4f}")
    print(f"report     : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
