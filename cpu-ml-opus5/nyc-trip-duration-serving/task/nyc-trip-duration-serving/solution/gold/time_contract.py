from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")


def _one(value):
    if value is None or pd.isna(value):
        return "UNKNOWN", "UNKNOWN", "missing"
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "UNKNOWN", "UNKNOWN", "invalid"
    if parsed.tzinfo is not None:
        local = parsed.astimezone(NY)
        return str(local.hour), str(local.weekday()), "instant"
    a = parsed.replace(tzinfo=NY, fold=0)
    b = parsed.replace(tzinfo=NY, fold=1)
    back_a = a.astimezone(ZoneInfo("UTC")).astimezone(NY).replace(tzinfo=None)
    back_b = b.astimezone(ZoneInfo("UTC")).astimezone(NY).replace(tzinfo=None)
    if back_a != parsed and back_b != parsed:
        return "UNKNOWN", "UNKNOWN", "nonexistent"
    if a.utcoffset() != b.utcoffset():
        return "UNKNOWN", "UNKNOWN", "ambiguous"
    return str(parsed.hour), str(parsed.weekday()), "wall"


def derive(values: pd.Series) -> pd.DataFrame:
    rows = [_one(value) for value in values]
    return pd.DataFrame(rows, index=values.index,
                        columns=["pickup_hour", "pickup_dayofweek", "time_quality"])
