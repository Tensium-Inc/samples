"""Train/score split for the backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd

RANDOM_SEED = 20241107
TEST_FRACTION = 0.25


def make_split(features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return boolean masks (train, score) over the rows of `features`."""
    rng = np.random.default_rng(RANDOM_SEED)
    n = len(features)
    idx = rng.permutation(n)
    cut = int(n * (1.0 - TEST_FRACTION))
    train = np.zeros(n, dtype=bool)
    score = np.zeros(n, dtype=bool)
    train[idx[:cut]] = True
    score[idx[cut:]] = True
    return train, score
