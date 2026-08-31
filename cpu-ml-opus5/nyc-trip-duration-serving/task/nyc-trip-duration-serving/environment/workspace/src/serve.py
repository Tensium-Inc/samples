from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import enrich, features, model

DATA = Path(__file__).resolve().parent.parent / "data"


def load_reference() -> tuple[pd.DataFrame, pd.DataFrame]:
    zones = pd.read_csv(DATA / "taxi_zone_lookup.csv")
    rates = pd.read_csv(DATA / "rate_codes.csv")
    return zones, rates


def enrich_batch(trips: pd.DataFrame) -> pd.DataFrame:
    zones, rates = load_reference()
    return enrich.enrich(trips, zones, rates)


def build_matrix(trips: pd.DataFrame) -> pd.DataFrame:
    zones, rates = load_reference()
    return features.build(trips, zones, rates)


def score_batch(estimator, trips: pd.DataFrame) -> pd.Series:
    matrix = build_matrix(trips)
    return pd.Series(estimator.predict(matrix), name="predicted_duration_minutes")
