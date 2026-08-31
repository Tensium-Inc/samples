"""GOLD split.py — temporal split honouring config.yaml `split.cutoff`.

This is the DECOY fix (D1). It is the obvious defect, it is what the visible smoke test and
the backtest respond to, and it is what a familiar-but-wrong solution stops at. It does NOT
affect the graded terminal, which is computed from the featurizer alone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CUTOFF = pd.Timestamp("2011-03-01")


def make_split(features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return boolean masks (train, score): everything fitted predates everything scored."""
    as_of = pd.to_datetime(features["as_of"])
    train = (as_of < CUTOFF).to_numpy()
    score = (as_of >= CUTOFF).to_numpy()
    return train, score
