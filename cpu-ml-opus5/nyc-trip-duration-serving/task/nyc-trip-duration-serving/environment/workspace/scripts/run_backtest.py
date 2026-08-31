from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import features, model, serve

HOLDOUT_FRACTION = 0.25


def main() -> int:
    trips = pd.read_parquet(ROOT / "data" / "trips.parquet")
    zones, rates = serve.load_reference()

    matrix = features.build(trips, zones, rates)
    y = features.target(trips)

    cut = int(len(matrix) * (1.0 - HOLDOUT_FRACTION))
    estimator = model.fit(matrix.iloc[:cut], y.iloc[:cut])
    metrics = model.evaluate(estimator, matrix.iloc[cut:], y.iloc[cut:])

    report = {"n_trips": int(len(trips)), "n_train": int(cut), **metrics}
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    (out / "backtest_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
