"""Helpers for laying out the (customer_id, as_of) evaluation grid."""
from __future__ import annotations

import pandas as pd


def probe_grid(tx: pd.DataFrame, start: str | pd.Timestamp, end: str | pd.Timestamp,
               freq: str = "MS", window_days: int = 90) -> pd.DataFrame:
    """One row per (customer_id, as_of) for accounts active in the trailing window."""
    tx = tx.copy()
    tx["InvoiceDate"] = pd.to_datetime(tx["InvoiceDate"])
    rows = []
    for ts in pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=freq):
        active = tx[(tx["InvoiceDate"] < ts) &
                    (tx["InvoiceDate"] >= ts - pd.Timedelta(days=window_days))]
        for cid in sorted(active["customer_id"].unique()):
            rows.append((int(cid), ts))
    return pd.DataFrame(rows, columns=["customer_id", "as_of"]).sort_values(
        ["as_of", "customer_id"], kind="stable").reset_index(drop=True)
