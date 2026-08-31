#!/usr/bin/env python3
"""Untrusted half of scoring. Runs as `runner`. Executes agent-authored code.

This process is assumed hostile. It is allowed exactly two things:

    read   /srv/deploy            the agent's own deployed code + fitted model
           /srv/handoff/features.csv   FEATURES ONLY -- the label column is not in it
    write  /srv/handoff/out/preds.csv  one predicted class per input row

It cannot reach /opt/env at all (that tree is root:envdata 0750 and `runner` is not in
envdata), so it cannot read the held-out labels, the pass bars, or the action log, and it
cannot write the state. Nothing it prints is trusted: the metric is computed by the daemon,
as root, against labels this process never sees.

Earlier revision of this file did the opposite -- it received the full frame including
`cover_type` and computed the metric itself, so `return df["cover_type"]` scored a perfect
terminal. That is the hole this split exists to close.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

DEPLOY = Path("/srv/deploy")
FEATURES = Path("/srv/handoff/features.csv")
OUT = Path("/srv/handoff/out/preds.csv")


def main() -> int:
    df = pd.read_csv(FEATURES)
    if "cover_type" in df.columns:            # belt and braces; the daemon drops it
        raise SystemExit("features frame must not carry the label column")

    sys.path.insert(0, str(DEPLOY))
    from covertype import config, predict     # deployed agent code

    cfg = config.load(DEPLOY / "configs" / "pipeline.yaml")
    cfg["_root"] = str(DEPLOY)

    pred = predict.predict_frame(df, cfg)
    out = pd.DataFrame({"cover_type_pred": pd.Series(list(pred))})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
