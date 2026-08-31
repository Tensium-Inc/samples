"""GOLD featurize.py — the reference fix. Author-side only; NEVER in environment/workspace/.

The reference implements the audited point-in-time contract, including line-stable joins and
separate effective/knowledge timestamps.

  1. `_attribution_map` resolves credit notes against remaining order-line capacity. Credits
     are processed in document order and pair to the most recent strictly earlier positive
     line with the same customer, SKU and unit-price cents that still has enough unallocated
     quantity. A successful match consumes capacity; an indivisible credit that cannot fit a
     single line is unattributable. Timestamp ties preserve stable source-row order.
  2. The trailing window's right edge is EXCLUSIVE — activity stamped exactly at `as_of` is
     not observable at `as_of`.
  3. EMBARGO_DAYS = 1, matching `settlement_lag_days` in config.yaml.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_COLUMNS = ["n_lines", "n_sku", "n_credit", "spend_cents", "days_since_last"]

WINDOW_DAYS = 90
EMBARGO_DAYS = 1          # config.yaml: settlement_lag_days
CACHE_NAME = "credit_attribution_v3-capacity-price.parquet"
CACHE_META_NAME = "credit_attribution_v3-capacity-price.json"


def _cache_dir(cache_dir: str | Path | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(os.environ.get("PIPELINE_CACHE_DIR", "caches"))


def _attribution_map(tx: pd.DataFrame, cache_dir: str | Path | None = None) -> pd.DataFrame:
    cdir = _cache_dir(cache_dir)
    cached = cdir / CACHE_NAME
    meta = cdir / CACHE_META_NAME
    if cached.exists() and meta.exists():
        try:
            expected = json.loads(meta.read_text())["sha256"]
            actual = hashlib.sha256(cached.read_bytes()).hexdigest()
            if actual == expected:
                return pd.read_parquet(cached)
        except (OSError, ValueError, KeyError):
            pass

    tx = tx.copy()
    tx["InvoiceDate"] = pd.to_datetime(tx["InvoiceDate"])
    if "is_credit" not in tx.columns:
        tx["is_credit"] = tx["Invoice"].astype(str).str.startswith("C")

    tx = tx.reset_index(drop=True)
    tx["_line_id"] = np.arange(len(tx), dtype="int64")
    tx["_price_cents"] = np.rint(tx["Price"].astype(float) * 100).astype("int64")
    pos = tx[~tx["is_credit"] & (tx["Quantity"] > 0)][
        ["_line_id", "customer_id", "StockCode", "_price_cents", "Invoice",
         "Quantity", "InvoiceDate"]].copy()
    cred = tx[tx["is_credit"] & (tx["Quantity"] < 0)][
        ["_line_id", "customer_id", "StockCode", "_price_cents", "Invoice",
         "Quantity", "InvoiceDate"]].copy()

    keys = ["customer_id", "StockCode", "_price_cents"]
    pools = {}
    for key, group in pos.groupby(keys, sort=False):
        rows = group.sort_values(["InvoiceDate", "_line_id"], kind="stable").to_dict("records")
        for row in rows:
            row["remaining"] = int(row["Quantity"])
        pools[key] = rows

    credit_dates = {}
    cred = cred.sort_values(["InvoiceDate", "_line_id"], kind="stable")
    for row in cred.to_dict("records"):
        key = (row["customer_id"], row["StockCode"], row["_price_cents"])
        need = abs(int(row["Quantity"]))
        eligible = [candidate for candidate in pools.get(key, [])
                    if candidate["InvoiceDate"] < row["InvoiceDate"]
                    and candidate["remaining"] >= need]
        if not eligible:
            credit_dates[row["_line_id"]] = pd.NaT
            continue
        chosen = max(eligible, key=lambda candidate: (
            candidate["InvoiceDate"], int(candidate["_line_id"])))
        chosen["remaining"] -= need
        credit_dates[row["_line_id"]] = chosen["InvoiceDate"]

    amap = tx[["_line_id", "Invoice", "StockCode", "customer_id", "InvoiceDate"]].copy()
    is_credit = tx["is_credit"].to_numpy()
    # positives keep their own date; credits take the reversed order's date (NaT if unpairable)
    amap["eff_date"] = amap["InvoiceDate"]
    mapped_dates = pd.to_datetime(amap.loc[is_credit, "_line_id"].map(credit_dates))
    amap.loc[is_credit, "eff_date"] = mapped_dates.to_numpy()

    cdir.mkdir(parents=True, exist_ok=True)
    amap.to_parquet(cached, index=False)
    meta.write_text(json.dumps({"sha256": hashlib.sha256(cached.read_bytes()).hexdigest()}))
    return amap


def build_features(tx: pd.DataFrame,
                   probes: pd.DataFrame,
                   cache_dir: str | Path | None = None) -> pd.DataFrame:
    tx = tx.copy()
    tx["InvoiceDate"] = pd.to_datetime(tx["InvoiceDate"])
    if "is_credit" not in tx.columns:
        tx["is_credit"] = tx["Invoice"].astype(str).str.startswith("C")

    amap = _attribution_map(tx, cache_dir)
    tx = tx.reset_index(drop=True)
    tx["_line_id"] = np.arange(len(tx), dtype="int64")
    tx = tx.merge(amap[["_line_id", "eff_date"]], on="_line_id", how="left",
                  validate="one_to_one")
    tx["eff_date"] = pd.to_datetime(tx["eff_date"])
    # Unattributable credits carry no effective date and are excluded from aggregates.
    tx = tx[tx["eff_date"].notna()]
    tx["revenue"] = tx["Quantity"] * tx["Price"]

    probes = probes.copy()
    probes["as_of"] = pd.to_datetime(probes["as_of"])

    out = []
    for as_of, grp in probes.groupby("as_of", sort=True):
        hi = as_of - pd.Timedelta(days=EMBARGO_DAYS)
        lo = hi - pd.Timedelta(days=WINDOW_DAYS)
        # A credit may affect the commercial date only once its later finance document is
        # knowable. This prevents future credits from rewriting historical snapshots.
        win = tx[(tx["InvoiceDate"] < hi) &
                 (tx["eff_date"] >= lo) & (tx["eff_date"] < hi)]
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
