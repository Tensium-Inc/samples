"""Fitting and persisting the classifier."""
from __future__ import annotations

import json
import hashlib

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from . import config, features

LABEL = "cover_type"


def sample_weights(cfg: dict, y: pd.Series) -> np.ndarray | None:
    """Per-class weights, raised to `class_weight_alpha`.

    alpha = 0 leaves every row weighted equally. alpha = 1 is the usual balanced weighting.
    Values in between trade majority-class accuracy for minority-class recall.
    """
    alpha = float(cfg["model"].get("class_weight_alpha", 0.0))
    if alpha == 0.0:
        return None
    classes = np.unique(y)
    base = {c: len(y) / (len(classes) * (y == c).sum()) for c in classes}
    return y.map({c: base[c] ** alpha for c in classes}).to_numpy()


def fit(cfg: dict, train: pd.DataFrame) -> HistGradientBoostingClassifier:
    X = features.build(cfg, train)
    y = train[LABEL]
    clf = HistGradientBoostingClassifier(
        max_iter=int(cfg["model"]["max_iter"]),
        learning_rate=float(cfg["model"]["learning_rate"]),
        random_state=int(cfg["model"]["seed"]),
        # Off on purpose: early stopping carves its own random validation slice, which makes
        # a fitted model non-reproducible run to run. The monthly retrain has to be exactly
        # reproducible, so it stays off.
        early_stopping=False,
    )
    clf.fit(X, y, sample_weight=sample_weights(cfg, y))
    return clf


def save(cfg: dict, clf, extra: dict | None = None) -> str:
    path = config.resolve(cfg, cfg["output"]["model_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": clf,
        "feature_columns": list(features.columns(cfg)),
        "feature_spec": features.spec(cfg),
        "class_weight_alpha": float(cfg["model"].get("class_weight_alpha", 0.0)),
        "meta": extra or {},
    }
    joblib.dump(payload, path)
    return str(path)


def load(cfg: dict) -> dict:
    return joblib.load(config.resolve(cfg, cfg["output"]["model_path"]))


def fingerprint(cfg: dict) -> str:
    """Stable identity of the fitted artifact: feature spec + model settings.

    The feature cache is keyed on this. Change the design matrix or the weighting and the
    fingerprint moves, which is what tells the serving side its cache is stale.
    """
    blob = json.dumps({
        "features": features.spec(cfg),
        "columns": features.columns(cfg),
        "model": {k: cfg["model"][k] for k in sorted(cfg["model"])},
        # The split decides which rows are fitted, so it changes the artifact just as much
        # as the feature spec does. Leaving it out meant a run that fixed the pipeline by
        # changing only the split produced a different model under an unchanged
        # fingerprint -- the serving cache never noticed, and the deployment could not be
        # told apart from the baseline.
        "split": {k: cfg["split"][k] for k in sorted(cfg["split"])},
        "data": {k: cfg["data"][k] for k in sorted(cfg["data"])},
    }, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
