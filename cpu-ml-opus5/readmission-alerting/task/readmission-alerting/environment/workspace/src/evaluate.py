"""Evaluation and final fit.

PUBLIC API - a harness imports these three by name. Keep the signatures stable:

    run_evaluation(df, cfg) -> dict with at least {"score": float}
    fit_final(df, cfg)      -> a fitted estimator
    predict_scores(model, df, cfg) -> ndarray of risk scores

`run_evaluation` produces the figure that goes to the care team.
"""
from __future__ import annotations

import pickle
from typing import Any, Dict

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

from .config import resolve
from .features import build_matrix
from .splitting import make_splits

CACHE_VERSION = 2


def _make_model(cfg: Dict[str, Any]) -> RandomForestClassifier:
    m = cfg["model"]
    return RandomForestClassifier(
        n_estimators=int(m["n_estimators"]),
        min_samples_leaf=int(m.get("min_samples_leaf", 1)),
        random_state=int(m["random_state"]),
        n_jobs=int(m.get("n_jobs", 1)),
    )


def _prepare(df: pd.DataFrame, cfg: Dict[str, Any]):
    """Build (or reuse) the design matrix and the fold assignment."""
    cache_path = resolve(cfg["features"]["cache_path"])
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            blob = pickle.load(f)
        if blob.get("version") == CACHE_VERSION and blob.get("n_rows") == len(df):
            return blob["X"], blob["y"], blob["folds"]

    X, y, _names = build_matrix(df, cfg)
    folds = make_splits(df, X, y, cfg)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump(
            {"version": CACHE_VERSION, "n_rows": len(df), "X": X, "y": y, "folds": folds}, f
        )
    return X, y, folds


def run_evaluation(df: pd.DataFrame, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-validated figure published to the care team."""
    memo_path = resolve(cfg["reporting"]["summary_memo"])
    if memo_path.exists():
        with open(memo_path, "rb") as f:
            memo = pickle.load(f)
        if memo.get("version") == CACHE_VERSION and memo.get("n_rows") == len(df):
            return dict(memo["report"])

    X, y, folds = _prepare(df, cfg)
    scores = []
    for train_idx, test_idx in folds:
        model = _make_model(cfg)
        model.fit(X[train_idx], y[train_idx])
        scores.append(accuracy_score(y[test_idx], model.predict(X[test_idx])))
    report = {
        "score": float(np.mean(scores)),
        "fold_scores": [float(s) for s in scores],
        "n_splits": len(folds),
        "metric": "accuracy",
    }
    memo_path.parent.mkdir(parents=True, exist_ok=True)
    with open(memo_path, "wb") as f:
        pickle.dump({"version": CACHE_VERSION, "n_rows": len(df), "report": report}, f)
    return report


def fit_final(df: pd.DataFrame, cfg: Dict[str, Any]) -> RandomForestClassifier:
    """Fit the deployable model on the whole cohort."""
    X, y, _folds = _prepare(df, cfg)
    model = _make_model(cfg)
    model.fit(X, y)
    return model


def predict_scores(
    model: RandomForestClassifier, df: pd.DataFrame, cfg: Dict[str, Any]
) -> np.ndarray:
    """Risk score per encounter, used to rank the follow-up list."""
    X, _y, _names = build_matrix(df, cfg)
    return model.predict_proba(X)[:, 1]
