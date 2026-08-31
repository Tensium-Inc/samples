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
from sklearn.metrics import average_precision_score

from .config import resolve
from .features import build_base, build_matrix, encode_nested
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
    """Build (or reuse) the outcome-independent matrix and the fold assignment.

    Only the part of the matrix that does not depend on outcomes is memoised. The
    rate-encoded columns are rebuilt per fold from that fold's training rows, so they
    cannot be cached here.
    """
    cache_path = resolve(cfg["features"]["cache_path"])
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            blob = pickle.load(f)
        if blob.get("version") == CACHE_VERSION and blob.get("n_rows") == len(df):
            return blob["X"], blob["y"], blob["folds"]

    X, y, _names = build_base(df, cfg)
    folds = make_splits(df, X, y, cfg)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump(
            {"version": CACHE_VERSION, "n_rows": len(df), "X": X, "y": y, "folds": folds}, f
        )
    return X, y, folds


def run_evaluation(df: pd.DataFrame, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-validated figure published to the care team.

    The rate encoding is learned on each fold's training rows only, and the figure is the
    metric named in reporting.policy_metric - the list is ranked and only its top is acted
    on, so ranking quality on a rare event is what matters.
    """
    memo_path = resolve(cfg["reporting"]["summary_memo"])
    if memo_path.exists():
        with open(memo_path, "rb") as f:
            memo = pickle.load(f)
        if memo.get("version") == CACHE_VERSION and memo.get("n_rows") == len(df):
            return dict(memo["report"])

    base, y, folds = _prepare(df, cfg)
    oof = np.full(len(y), np.nan)
    scores = []
    for train_idx, test_idx in folds:
        enc = encode_nested(train_idx, df, cfg)
        X = np.hstack([base, enc])
        model = _make_model(cfg)
        model.fit(X[train_idx], y[train_idx])
        proba = model.predict_proba(X[test_idx])[:, 1]
        oof[test_idx] = proba
        scores.append(average_precision_score(y[test_idx], proba))
    report = {
        "score": float(average_precision_score(y, oof)),
        "fold_scores": [float(s) for s in scores],
        "n_splits": len(folds),
        "metric": str(cfg["reporting"]["policy_metric"]),
    }
    memo_path.parent.mkdir(parents=True, exist_ok=True)
    with open(memo_path, "wb") as f:
        pickle.dump({"version": CACHE_VERSION, "n_rows": len(df), "report": report}, f)
    return report


def fit_final(df: pd.DataFrame, cfg: Dict[str, Any]) -> RandomForestClassifier:
    """Fit the deployable model on the whole cohort."""
    X, y, _names = build_matrix(df, cfg)
    model = _make_model(cfg)
    model.fit(X, y)
    return model


def predict_scores(
    model: RandomForestClassifier, df: pd.DataFrame, cfg: Dict[str, Any]
) -> np.ndarray:
    """Risk score per encounter, used to rank the follow-up list."""
    X, _y, _names = build_matrix(df, cfg)
    return model.predict_proba(X)[:, 1]
