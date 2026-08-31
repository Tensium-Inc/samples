"""Inference entry point.

This is the contract the serving side calls. It must stay importable and keep this
signature: the deployment harness loads it in a fresh process and hands it a frame of
cells to score.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, features, model


def predict_frame(df: pd.DataFrame, cfg: dict | None = None) -> np.ndarray:
    """Return one predicted cover type per row of `df`, in row order."""
    cfg = cfg or config.load()
    payload = model.load(cfg)
    X = features.build(cfg, df)
    expected = payload["feature_columns"]
    if list(X.columns) != list(expected):
        raise ValueError(
            "design matrix does not match the fitted model: "
            f"built {len(X.columns)} columns, model expects {len(expected)}. "
            "The feature spec used at inference must match the one used at fit time."
        )
    return payload["model"].predict(X)
