"""Encounter ingest and cohort selection - reference.

The observable-window rule is the one the shipped code gets wrong. Its intent is right -
keep every encounter except each patient's most recent - but it spends `duplicated(...,
keep="last")`, which drops the last occurrence in ROW ORDER. The export is written in no
particular order, so that removes an arbitrary encounter per patient instead of the latest
one: same row count, a third of the rows different, and a third of the censored encounters
left in the cohort. Recency has to come from `encounter_id`, not from position.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from .config import resolve


def _apply_cohort(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    rules = cfg.get("cohort", {})
    if rules.get("exclude_missing_sex", False):
        df = df[df["gender"].isin(["Male", "Female"])]
    if rules.get("require_observable_window", False):
        last = df.groupby("patient_nbr")["encounter_id"].transform("max")
        df = df[df["encounter_id"] != last]
    return df.reset_index(drop=True)


def load_encounters(cfg: Dict[str, Any]) -> pd.DataFrame:
    df = pd.read_csv(resolve(cfg["dataset"]["path"]), low_memory=False)
    if df.empty:
        raise ValueError("encounter table is empty")
    return _apply_cohort(df, cfg)
