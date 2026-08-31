"""Design-matrix construction.

Diagnosis codes and specialty are represented by the observed readmission rate of each
level, which keeps the matrix narrow where one-hot encoding would add thousands of columns.

Those rates are computed from outcomes, so they are fitted on a training split and applied
to the rows being scored - never derived from the rows they will later be used to score.
`build_base` and `encode_from` exist so a caller can do that per fold; `build_matrix` is
kept for callers that only need a matrix for a final fit on the whole cohort.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def add_derived(frame: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    if "diag_triple" in cfg["features"]["target_encoded"] and "diag_triple" not in frame:
        frame = frame.copy()
        frame["diag_triple"] = (frame["diag_1"].astype(str) + "|"
                                + frame["diag_2"].astype(str) + "|"
                                + frame["diag_3"].astype(str))
    return frame


def target_rates(frame: pd.DataFrame, cfg: Dict[str, Any]) -> Dict[str, pd.Series]:
    """Readmission rate per level, for each high-cardinality column."""
    target = cfg["dataset"]["target"]
    return {c: frame.groupby(c)[target].mean() for c in cfg["features"]["target_encoded"]}


def apply_rates(frame: pd.DataFrame, rates: Dict[str, pd.Series], prior: float) -> np.ndarray:
    """Map each level to its rate; levels never seen fall back to the overall rate."""
    cols = [frame[c].map(rates[c]).fillna(prior).to_numpy(dtype=float) for c in rates]
    return np.column_stack(cols)


def build_base(df: pd.DataFrame, cfg: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """The part of the matrix that does not depend on outcomes."""
    df = add_derived(df, cfg)
    feats = cfg["features"]
    y = df[cfg["dataset"]["target"]].to_numpy()
    numeric = df[feats["numeric"]].to_numpy(dtype=float)
    cat = pd.get_dummies(df[feats["categorical"]].astype(str), drop_first=True)
    base = np.hstack([numeric, cat.to_numpy(dtype=float)])
    return base, y, list(feats["numeric"]) + list(cat.columns)


def encode_from(
    train: pd.DataFrame, target_frame: pd.DataFrame, cfg: Dict[str, Any]
) -> np.ndarray:
    """Rates learned on `train`, applied to `target_frame`."""
    train = add_derived(train, cfg)
    target_frame = add_derived(target_frame, cfg)
    prior = float(train[cfg["dataset"]["target"]].mean())
    return apply_rates(target_frame, target_rates(train, cfg), prior)


def encode_nested(
    train_idx: np.ndarray, df: pd.DataFrame, cfg: Dict[str, Any]
) -> np.ndarray:
    """Encoding under features.target_encoding, aligned to every row of `df`.

    Rates fitted on the training rows alone still carry each training row's own outcome,
    which at this cardinality is most of the level's mass: 7718 of 8703 diag_triple levels
    occur once. Those rows get their own label back as a feature, the forest reads it, and
    the published figure rises with no gain on unseen rows. Training rows are therefore
    encoded from inner folds that exclude them; every other row is encoded from the whole
    training set.
    """
    from sklearn.model_selection import KFold

    inner_k = 5
    target = cfg["dataset"]["target"]
    df = add_derived(df, cfg)
    train = df.iloc[train_idx].reset_index(drop=True)
    prior = float(train[target].mean())

    out = []
    for c in cfg["features"]["target_encoded"]:
        column = df[c].map(target_rates(train, cfg)[c]).fillna(prior).to_numpy(dtype=float)
        inner = np.full(len(train), prior)
        for itr, ite in KFold(n_splits=inner_k, shuffle=True, random_state=0).split(train):
            rates = train.iloc[itr].groupby(c)[target].mean()
            inner[ite] = train.iloc[ite][c].map(rates).fillna(prior).to_numpy(dtype=float)
        column[np.asarray(train_idx)] = inner
        out.append(column)
    return np.column_stack(out)


def build_matrix(
    df: pd.DataFrame, cfg: Dict[str, Any]
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Whole-cohort matrix, for a final fit where there is no held-out split to protect."""
    base, y, names = build_base(df, cfg)
    encoded = encode_from(df, df, cfg)
    X = np.hstack([base, encoded])
    if np.isnan(X).any():
        raise ValueError("design matrix contains missing values")
    return X, y, names + list(cfg["features"]["target_encoded"])
