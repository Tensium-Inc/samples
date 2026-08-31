"""Design-matrix construction."""
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


def build_matrix(
    df: pd.DataFrame, cfg: Dict[str, Any]
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Return (X, y, feature_names) for the whole encounter table."""
    target = cfg["dataset"]["target"]
    feats = cfg["features"]

    df = add_derived(df, cfg)
    y = df[target].to_numpy()
    prior = float(y.mean())

    numeric = df[feats["numeric"]].to_numpy(dtype=float)
    cat = pd.get_dummies(df[feats["categorical"]].astype(str), drop_first=True)

    rates = target_rates(df, cfg)
    encoded = apply_rates(df, rates, prior)

    X = np.hstack([numeric, cat.to_numpy(dtype=float), encoded])
    names = list(feats["numeric"]) + list(cat.columns) + list(feats["target_encoded"])
    if np.isnan(X).any():
        raise ValueError("design matrix contains missing values")
    return X, y, names
