"""Alternate correct temporal contract.

Independent of solution/gold's fold-arithmetic classifier: uses pandas tz_localize with
raising semantics to detect the spring-forward gap and the fall-back repeat. Must produce
byte-identical hour / dayofweek / quality to the gold on the graded populations.
"""
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
    stamp = pd.Timestamp(parsed)
    try:
        stamp.tz_localize(NY, ambiguous=False, nonexistent="raise")
    except pd.errors.OutOfBoundsDatetime:
        raise
    except Exception:
        return "UNKNOWN", "UNKNOWN", "nonexistent"
    earlier = stamp.tz_localize(NY, ambiguous=False).utcoffset()
    later = stamp.tz_localize(NY, ambiguous=True).utcoffset()
    if earlier != later:
        return "UNKNOWN", "UNKNOWN", "ambiguous"
    return str(parsed.hour), str(parsed.weekday()), "wall"


def derive(values: pd.Series) -> pd.DataFrame:
    rows = [_one(value) for value in values]
    return pd.DataFrame(rows, index=values.index,
                        columns=["pickup_hour", "pickup_dayofweek", "time_quality"])
