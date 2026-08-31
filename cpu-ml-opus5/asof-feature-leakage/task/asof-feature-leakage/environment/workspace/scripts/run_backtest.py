#!/usr/bin/env python3
"""Run the repeat-purchase backtest and write results/backtest_report.json.

    python3 scripts/run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import featurize, model, panel, split  # noqa: E402

BACKTEST_START = "2010-04-01"
BACKTEST_END = "2011-06-01"


def main() -> int:
    tx = pd.read_parquet(ROOT / "data" / "transactions.parquet")
    probes = panel.probe_grid(tx, BACKTEST_START, BACKTEST_END)

    features = featurize.build_features(tx, probes)
    labels = featurize.build_labels(tx, probes)
    train, score = split.make_split(features)
    metrics = model.fit_score(features, labels.to_numpy(), train, score)

    report = {
        "n_probes": int(len(probes)),
        "as_of_stamps": int(probes["as_of"].nunique()),
        **metrics,
    }
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "backtest_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
