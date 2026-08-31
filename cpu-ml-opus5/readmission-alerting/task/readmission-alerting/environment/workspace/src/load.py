"""Encounter ingest and cohort selection."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from .config import resolve


def _apply_cohort(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    rules = cfg.get("cohort", {})
    if rules.get("exclude_missing_sex", False):
        df = df[df["gender"].isin(["Male", "Female"])]
    if rules.get("require_observable_window", False):
        df = df[df.duplicated("patient_nbr", keep="last")]
    return df.reset_index(drop=True)


def load_encounters(cfg: Dict[str, Any]) -> pd.DataFrame:
    df = pd.read_csv(resolve(cfg["dataset"]["path"]), low_memory=False)
    if df.empty:
        raise ValueError("encounter table is empty")
    return _apply_cohort(df, cfg)
