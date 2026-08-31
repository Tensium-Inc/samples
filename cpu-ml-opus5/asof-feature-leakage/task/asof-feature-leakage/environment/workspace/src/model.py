"""Model definition for the repeat-purchase backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from . import featurize

RANDOM_SEED = 20241107


def make_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=120, max_depth=4, learning_rate=0.1,
        random_state=RANDOM_SEED, early_stopping=False,
    )


def fit_score(features: pd.DataFrame, labels: np.ndarray,
              train: np.ndarray, score: np.ndarray) -> dict:
    """Fit on `train`, evaluate on `score`. Returns a small metric dict."""
    X = features[featurize.FEATURE_COLUMNS].to_numpy()
    y = np.asarray(labels)
    model = make_model()
    model.fit(X[train], y[train])
    proba = model.predict_proba(X[score])[:, 1]
    return {
        "n_train": int(train.sum()),
        "n_score": int(score.sum()),
        "positive_rate": float(y[score].mean()) if score.sum() else 0.0,
        "auc": float(roc_auc_score(y[score], proba)) if len(set(y[score])) > 1 else float("nan"),
    }
