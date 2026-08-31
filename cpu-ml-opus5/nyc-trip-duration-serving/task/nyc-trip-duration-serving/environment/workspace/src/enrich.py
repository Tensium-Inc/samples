from __future__ import annotations

import pandas as pd

UNKNOWN = "UNKNOWN"

ENRICHED_COLUMNS = ["pu_borough", "pu_service_zone", "do_borough",
                    "do_service_zone", "rate_class", "fare_basis"]

DEFAULT_RATE_CODE = 99
FALLBACK_RATE_CLASS = "standard_rate"
FALLBACK_FARE_BASIS = "metered"


def _zone_table(zones: pd.DataFrame) -> pd.DataFrame:
    return zones.set_index("LocationID")


def _rate_table(rates: pd.DataFrame) -> pd.DataFrame:
    return rates.set_index("code")


def enrich(trips: pd.DataFrame, zones: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    zone_table = _zone_table(zones)
    rate_table = _rate_table(rates)

    out = trips.copy()
    out["pu_borough"] = trips["PULocationID"].map(zone_table["Borough"]).fillna("N/A")
    out["pu_service_zone"] = trips["PULocationID"].map(zone_table["service_zone"]).fillna("N/A")
    out["do_borough"] = trips["DOLocationID"].map(zone_table["Borough"]).fillna("N/A")
    out["do_service_zone"] = trips["DOLocationID"].map(zone_table["service_zone"]).fillna("N/A")

    code = trips["RatecodeID"].fillna(DEFAULT_RATE_CODE).astype("int64")
    out["rate_class"] = code.map(rate_table["rate_class"]).fillna(FALLBACK_RATE_CLASS)
    out["fare_basis"] = code.map(rate_table["fare_basis"]).fillna(FALLBACK_FARE_BASIS)
    return out
