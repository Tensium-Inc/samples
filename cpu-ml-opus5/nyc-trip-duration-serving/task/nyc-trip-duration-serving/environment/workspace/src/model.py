from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from . import features

SEED = 8317


def build_estimator() -> Pipeline:
    encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    pre = ColumnTransformer(
        [("cat", encoder, features.CATEGORICAL)],
        remainder="passthrough",
        verbose_feature_names_out=False,
    )
    model = HistGradientBoostingRegressor(
        max_iter=150, max_depth=6, learning_rate=0.1,
        random_state=SEED, early_stopping=False,
    )
    return Pipeline([("pre", pre), ("model", model)])


def fit(matrix: pd.DataFrame, y: pd.Series) -> Pipeline:
    estimator = build_estimator()
    estimator.fit(matrix, np.asarray(y, dtype="float64"))
    return estimator


def evaluate(estimator: Pipeline, matrix: pd.DataFrame, y: pd.Series) -> dict:
    predicted = estimator.predict(matrix)
    truth = np.asarray(y, dtype="float64")
    return {
        "n": int(len(truth)),
        "mae": float(mean_absolute_error(truth, predicted)),
        "median_abs_error": float(np.median(np.abs(truth - predicted))),
    }
