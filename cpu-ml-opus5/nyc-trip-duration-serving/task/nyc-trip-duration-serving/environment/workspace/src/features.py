from __future__ import annotations

import numpy as np
import pandas as pd

from . import enrich, time_contract

CATEGORICAL = list(enrich.ENRICHED_COLUMNS) + ["time_quality"]
NUMERIC = ["trip_distance", "passenger_count", "pickup_hour", "pickup_dayofweek"]
TARGET = "duration_minutes"


def add_time_parts(trips: pd.DataFrame) -> pd.DataFrame:
    out = trips.copy()
    temporal = time_contract.derive(out["lpep_pickup_datetime"])
    out["pickup_hour"] = pd.to_numeric(temporal["pickup_hour"], errors="coerce").fillna(-1)
    out["pickup_dayofweek"] = pd.to_numeric(
        temporal["pickup_dayofweek"], errors="coerce").fillna(-1)
    out["time_quality"] = temporal["time_quality"].astype(str)
    return out


def target(trips: pd.DataFrame) -> pd.Series:
    pickup = pd.to_datetime(trips["lpep_pickup_datetime"])
    dropoff = pd.to_datetime(trips["lpep_dropoff_datetime"])
    return ((dropoff - pickup).dt.total_seconds() / 60.0).rename(TARGET)


def build(trips: pd.DataFrame, zones: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    enriched = enrich.enrich(trips, zones, rates)
    framed = add_time_parts(enriched)
    columns = CATEGORICAL + NUMERIC
    out = framed.loc[:, columns].copy()
    for column in CATEGORICAL:
        out[column] = out[column].astype(str)
    out["passenger_count"] = pd.to_numeric(
        out["passenger_count"], errors="coerce").fillna(1.0).astype("float64")
    out["trip_distance"] = pd.to_numeric(
        out["trip_distance"], errors="coerce").fillna(0.0).astype("float64")
    return out.reset_index(drop=True)
