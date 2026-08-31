#!/usr/bin/env python3
"""Retrain the cover-type classifier and report holdout metrics.

    python3 scripts/train.py [--config configs/pipeline.yaml]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from covertype import config, data, evaluate, model


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cfg = config.load(args.config)
    df = data.load_cells(cfg)
    train, holdout = data.split(cfg, df)

    clf = model.fit(cfg, train)
    metrics = evaluate.evaluate(cfg, clf, holdout)
    path = model.save(cfg, clf, extra={"holdout": metrics, "n_train": int(len(train))})

    if not args.quiet:
        print(f"  rows        : {len(df):,}  train {len(train):,} / holdout {len(holdout):,}")
        print(f"  split       : {cfg['split']['strategy']}")
        print(f"  features    : {json.dumps(cfg['features'])}")
        print(f"  weighting   : alpha={cfg['model'].get('class_weight_alpha', 0.0)}")
        print(f"  fingerprint : {model.fingerprint(cfg)}")
        print(f"  holdout     : accuracy {metrics['accuracy']:.4f}  "
              f"macro-recall {metrics['macro_recall']:.4f}")
        print(f"  per-class   : {metrics['per_class_recall']}")
        print(f"  saved       : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
