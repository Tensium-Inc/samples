"""Loading the cell table and carving out a holdout."""
from __future__ import annotations

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split

from . import config

LABEL = "cover_type"


def load_cells(cfg: dict) -> pd.DataFrame:
    return pd.read_csv(config.resolve(cfg, cfg["data"]["cells"]))


def split(cfg: dict, df: pd.DataFrame):
    """Carve a holdout out of the cell table.

    `random` shuffles rows. `grouped` keeps every cell from one group on the same side,
    which is the honest option when the groups are spatial.
    """
    s = cfg["split"]
    if s["strategy"] == "grouped":
        gss = GroupShuffleSplit(n_splits=1, test_size=s["test_size"], random_state=s["seed"])
        tr, te = next(gss.split(df, df[LABEL], groups=df[s["group_column"]]))
        return df.iloc[tr].reset_index(drop=True), df.iloc[te].reset_index(drop=True)
    if s["strategy"] == "random":
        tr, te = train_test_split(
            df, test_size=s["test_size"], random_state=s["seed"], stratify=df[LABEL]
        )
        return tr.reset_index(drop=True), te.reset_index(drop=True)
    raise ValueError(f"unknown split strategy: {s['strategy']}")
