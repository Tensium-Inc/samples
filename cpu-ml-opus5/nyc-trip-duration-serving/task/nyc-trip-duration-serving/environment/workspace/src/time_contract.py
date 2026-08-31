from __future__ import annotations

import pandas as pd


def derive(values: pd.Series) -> pd.DataFrame:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    return pd.DataFrame({
        "pickup_hour": parsed.dt.hour.fillna(-1).astype("int64").astype(str),
        "pickup_dayofweek": parsed.dt.dayofweek.fillna(-1).astype("int64").astype(str),
        "time_quality": parsed.notna().map({True: "valid", False: "invalid"}),
    }, index=values.index)
