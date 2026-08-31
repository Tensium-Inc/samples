"""Trailing-window behavioural features for the repeat-purchase model.

For each (customer_id, as_of) pair we summarise the account's recent activity over the
trailing window defined in config.yaml, and the model predicts whether the customer places
another order within the horizon.

Attribution note
----------------
Credit notes are separate documents raised by finance, so a line's *document date* and the
date the underlying activity happened are not always the same thing. `_attribution_map`
resolves a line to its effective date. Because resolving it is a self-join over the whole
panel, the result is memoised under `caches/` and reused across runs.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_COLUMNS = ["n_lines", "n_sku", "n_credit", "spend_cents", "days_since_last"]

WINDOW_DAYS = 90
EMBARGO_DAYS = 0
CACHE_NAME = "credit_attribution.parquet"


def _cache_dir(cache_dir: str | Path | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(os.environ.get("PIPELINE_CACHE_DIR", "caches"))


def _attribution_map(tx: pd.DataFrame, cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Map every line to the date its activity should be counted against.

    Returns a frame with columns [Invoice, StockCode, customer_id, InvoiceDate, eff_date].
    Memoised: if `caches/credit_attribution.parquet` exists it is reused as-is.
    """
    cdir = _cache_dir(cache_dir)
    cached = cdir / CACHE_NAME
    if cached.exists():
        return pd.read_parquet(cached)

    amap = tx[["Invoice", "StockCode", "customer_id", "InvoiceDate"]].copy()
    amap["eff_date"] = amap["InvoiceDate"]

    cdir.mkdir(parents=True, exist_ok=True)
    amap.to_parquet(cached, index=False)
    return amap


def build_features(tx: pd.DataFrame,
                   probes: pd.DataFrame,
                   cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Trailing-window features for each (customer_id, as_of) row of `probes`.

    Returns an integer frame indexed by (customer_id, as_of) with FEATURE_COLUMNS.
    """
    tx = tx.copy()
    tx["InvoiceDate"] = pd.to_datetime(tx["InvoiceDate"])
    if "is_credit" not in tx.columns:
        tx["is_credit"] = tx["Invoice"].astype(str).str.startswith("C")

    amap = _attribution_map(tx, cache_dir)
    tx = tx.merge(amap[["Invoice", "StockCode", "customer_id", "eff_date"]],
                  on=["Invoice", "StockCode", "customer_id"], how="left")
    tx["eff_date"] = pd.to_datetime(tx["eff_date"]).fillna(tx["InvoiceDate"])
    tx["revenue"] = tx["Quantity"] * tx["Price"]

    probes = probes.copy()
    probes["as_of"] = pd.to_datetime(probes["as_of"])

    out = []
    for as_of, grp in probes.groupby("as_of", sort=True):
        hi = as_of - pd.Timedelta(days=EMBARGO_DAYS)
        lo = hi - pd.Timedelta(days=WINDOW_DAYS)
        win = tx[(tx["eff_date"] >= lo) & (tx["eff_date"] <= hi)]
        agg = win.groupby("customer_id").agg(
            n_lines=("Quantity", "size"),
            n_sku=("StockCode", "nunique"),
            n_credit=("is_credit", "sum"),
            revenue=("revenue", "sum"),
            last_seen=("eff_date", "max"),
        )
        block = grp.set_index("customer_id").join(agg)
        block["n_lines"] = block["n_lines"].fillna(0)
        block["n_sku"] = block["n_sku"].fillna(0)
        block["n_credit"] = block["n_credit"].fillna(0)
        block["spend_cents"] = np.rint(block["revenue"].fillna(0.0) * 100)
        gap = (as_of - block["last_seen"]).dt.days
        block["days_since_last"] = gap.fillna(-1)
        out.append(block.reset_index()[["customer_id", "as_of", *FEATURE_COLUMNS]])

    feats = pd.concat(out, ignore_index=True)
    for c in FEATURE_COLUMNS:
        feats[c] = feats[c].astype("int64")
    return feats.sort_values(["as_of", "customer_id"], kind="stable").reset_index(drop=True)


def build_labels(tx: pd.DataFrame, probes: pd.DataFrame, horizon_days: int = 30) -> pd.Series:
    """1 if the customer placed a non-credit order within `horizon_days` after as_of."""
    tx = tx.copy()
    tx["InvoiceDate"] = pd.to_datetime(tx["InvoiceDate"])
    orders = tx[~tx["Invoice"].astype(str).str.startswith("C")]
    probes = probes.copy()
    probes["as_of"] = pd.to_datetime(probes["as_of"])
    out = []
    for as_of, grp in probes.groupby("as_of", sort=True):
        nxt = orders[(orders["InvoiceDate"] > as_of) &
                     (orders["InvoiceDate"] <= as_of + pd.Timedelta(days=horizon_days))]
        buyers = set(nxt["customer_id"].unique())
        out.append(grp["customer_id"].isin(buyers).astype("int64"))
    return pd.concat(out, ignore_index=True)
